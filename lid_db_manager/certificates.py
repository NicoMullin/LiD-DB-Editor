"""Which certificates HTTPS checks GitHub against, on Linux.

The Linux build carries its own OpenSSL, and OpenSSL looks for the list of
trusted certificates where the build machine (Ubuntu) keeps it. Other
distributions keep it elsewhere - Fedora under /etc/pki, openSUSE under
/etc/ssl - so there every download failed with "certificate verify failed".

At start, when that list is not where OpenSSL looks, this points OpenSSL at
the system's own list (SSL_CERT_FILE), or failing that at the copy the
``certifi`` package carries. Windows needs none of this: Python asks Windows
for its certificates. A copy run from source uses the system's own Python and
OpenSSL, which already know where to look, so nothing changes for it.
"""

from __future__ import annotations

import os
import ssl
import sys
from typing import Callable, MutableMapping

# Where each family of Linux keeps its list of trusted certificates.
SYSTEM_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",                   # Debian, Ubuntu, Mint, Arch, SteamOS
    "/etc/pki/tls/certs/ca-bundle.crt",                     # Fedora, RHEL
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",    # Fedora, RHEL
    "/etc/ca-certificates/extracted/tls-ca-bundle.pem",     # Arch, Manjaro
    "/etc/ssl/ca-bundle.pem",                               # openSUSE
    "/var/lib/ca-certificates/ca-bundle.pem",               # openSUSE
    "/etc/ssl/cert.pem",                                    # Alpine, Void
)


def _certifi_bundle() -> str:
    try:
        import certifi
    except ImportError:
        return ""
    try:
        return certifi.where()
    except Exception:  # noqa: BLE001 - only a fallback
        return ""


def use_system_certificates(
    environ: MutableMapping[str, str] | None = None,
    platform: str | None = None,
    is_file: Callable[[str], bool] = os.path.isfile,
    default_cafile: str | None = None,
    certifi_bundle: Callable[[], str] = _certifi_bundle,
) -> str:
    """Point OpenSSL at a certificate list that exists, when its own does not.

    Returns the file it chose, or "" when nothing needed changing: not Linux,
    the player set SSL_CERT_FILE or SSL_CERT_DIR themselves, or OpenSSL's own
    list is there.
    """
    environ = os.environ if environ is None else environ
    platform = sys.platform if platform is None else platform
    if not platform.startswith("linux"):
        return ""
    if environ.get("SSL_CERT_FILE") or environ.get("SSL_CERT_DIR"):
        return ""
    cafile = ssl.get_default_verify_paths().cafile if default_cafile is None else default_cafile
    if cafile and is_file(cafile):
        return ""
    for bundle in SYSTEM_BUNDLES:
        if is_file(bundle):
            environ["SSL_CERT_FILE"] = bundle
            return bundle
    bundle = certifi_bundle()
    if bundle and is_file(bundle):
        environ["SSL_CERT_FILE"] = bundle
        return bundle
    return ""
