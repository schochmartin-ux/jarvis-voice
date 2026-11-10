"""Verification of requests signed by the Alexa Skills Kit.

The skill endpoint cannot use Home Assistant's own authentication: Alexa posts
to it directly and has no way to send a bearer token. Amazon's answer is a
signature over the request body, and checking it is what keeps the open
endpoint from accepting commands from anybody who guesses the URL.

https://developer.amazon.com/en-US/docs/alexa/custom-skills/host-a-custom-skill-as-a-web-service.html
"""

from __future__ import annotations

import base64
import posixpath
from datetime import timedelta
from urllib.parse import urlparse

import aiohttp
import certifi
from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509 import Certificate, DNSName, load_pem_x509_certificates
from cryptography.x509.verification import PolicyBuilder, Store, VerificationError

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import LOGGER

SIGNATURE_HEADER = "Signature-256"
CERT_CHAIN_HEADER = "SignatureCertChainUrl"

# The chain always lives in the same bucket, and accepting any other host would
# let a caller point us at a certificate they control.
CERT_HOST = "s3.amazonaws.com"
CERT_PATH = "/echo.api/"
CERT_SAN = "echo-api.amazon.com"

# Amazon requires rejecting requests older than this, which is what stops a
# captured request from being replayed later.
TIMESTAMP_TOLERANCE = timedelta(seconds=150)

# Signing certificates are reused for long periods, so the chain is fetched and
# validated once per URL. A handful of entries covers a rotation.
_MAX_CACHE = 8


class AlexaAuthError(Exception):
    """Raised when a request cannot be attributed to Alexa."""


_cert_cache: dict[str, Certificate] = {}
_trust_store: Store | None = None


def _load_trust_store() -> Store:
    """Read the CA bundle. Blocking, so call from the executor."""
    with open(certifi.where(), "rb") as file:
        return Store(load_pem_x509_certificates(file.read()))


def _validate_cert_url(url: str) -> None:
    """Check the certificate URL before fetching anything from it."""
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise AlexaAuthError(f"certificate URL is not https: {url}")
    if (parsed.hostname or "").lower() != CERT_HOST:
        raise AlexaAuthError(f"certificate URL has an unexpected host: {url}")
    if (parsed.port or 443) != 443:
        raise AlexaAuthError(f"certificate URL has an unexpected port: {url}")
    # Normalise first so that /echo.api/../evil does not pass the prefix test.
    if not posixpath.normpath(parsed.path).startswith(CERT_PATH):
        raise AlexaAuthError(f"certificate URL has an unexpected path: {url}")


def _validated_leaf(pem: bytes, store: Store) -> Certificate:
    """Return the signing certificate of a chain that is trusted and valid.

    Blocking, so call from the executor.
    """
    try:
        certificates = load_pem_x509_certificates(pem)
    except ValueError as err:
        raise AlexaAuthError(f"certificate chain is not readable: {err}") from err
    if not certificates:
        raise AlexaAuthError("certificate chain is empty")

    leaf, *intermediates = certificates
    # Verifies the chain up to a trusted root, that nothing has expired, and
    # that the leaf really is Amazon's signing certificate.
    verifier = PolicyBuilder().store(store).build_server_verifier(DNSName(CERT_SAN))
    try:
        verifier.verify(leaf, intermediates)
    except VerificationError as err:
        raise AlexaAuthError(f"certificate chain is not trusted: {err}") from err
    return leaf


async def _async_signing_cert(hass: HomeAssistant, url: str) -> Certificate:
    """Return the certificate that signs requests, fetching it if needed."""
    global _trust_store  # noqa: PLW0603 - process wide cache of the CA bundle

    if (cached := _cert_cache.get(url)) is not None:
        # A cached chain was trusted when it was fetched, but it can expire
        # while it sits here.
        if cached.not_valid_after_utc > dt_util.utcnow():
            return cached
        del _cert_cache[url]

    try:
        response = await async_get_clientsession(hass).get(
            url, timeout=aiohttp.ClientTimeout(total=5)
        )
        response.raise_for_status()
        pem = await response.read()
    except (TimeoutError, aiohttp.ClientError) as err:
        raise AlexaAuthError(f"cannot fetch the certificate chain: {err}") from err

    if _trust_store is None:
        _trust_store = await hass.async_add_executor_job(_load_trust_store)

    leaf = await hass.async_add_executor_job(_validated_leaf, pem, _trust_store)

    if len(_cert_cache) >= _MAX_CACHE:
        _cert_cache.clear()
    _cert_cache[url] = leaf
    LOGGER.debug("Cached the Alexa signing certificate from %s", url)
    return leaf


def _verify_signature(leaf: Certificate, signature: bytes, body: bytes) -> None:
    """Check that the body was signed by the certificate's private key."""
    key = leaf.public_key()
    if not isinstance(key, rsa.RSAPublicKey):
        raise AlexaAuthError("signing certificate does not carry an RSA key")
    try:
        key.verify(signature, body, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as err:
        raise AlexaAuthError("signature does not match the request body") from err


def verify_timestamp(timestamp: str | None) -> None:
    """Reject a request that is too old to be genuine."""
    if not timestamp:
        raise AlexaAuthError("request has no timestamp")
    parsed = dt_util.parse_datetime(timestamp)
    if parsed is None:
        raise AlexaAuthError(f"request timestamp is not a datetime: {timestamp}")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.UTC)
    if abs(dt_util.utcnow() - parsed) > TIMESTAMP_TOLERANCE:
        raise AlexaAuthError(f"request timestamp is out of tolerance: {timestamp}")


async def async_verify_request(hass: HomeAssistant, headers: object, body: bytes) -> None:
    """Raise AlexaAuthError unless the body was signed by Alexa.

    ``headers`` is anything with a case-insensitive ``get``, which is what
    aiohttp hands us.
    """
    get = getattr(headers, "get", None)
    if get is None:
        raise AlexaAuthError("request has no headers")

    url = get(CERT_CHAIN_HEADER)
    encoded = get(SIGNATURE_HEADER)
    if not url or not encoded:
        raise AlexaAuthError("request is not signed")

    _validate_cert_url(url)
    try:
        signature = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as err:
        raise AlexaAuthError(f"signature is not base64: {err}") from err

    leaf = await _async_signing_cert(hass, url)
    _verify_signature(leaf, signature, body)
