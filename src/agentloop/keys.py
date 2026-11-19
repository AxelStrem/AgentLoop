"""The agents' key, pasted in and kept in a file only this person can read (item paste-key).

A worker or a loop reaches its Overseer server with a key file: `overseer.keyFile` holds its path,
never the key. Making such a file private by hand is fiddly, on Windows above all, so the page and
`agentloop add` take the key itself and the app writes the file: `keys/<host>-<account>.key` in its
config folder.

The key goes to the server once first, `GET /account/me`, as the check that it signs in; nothing is
written for one that does not, and the account it names is what the file is called by. It is never
written to the settings, a log, or an answer - only to its file. Replacing a key overwrites its file.

The file is private as each system makes one:
- macOS and Linux: the `keys` folder is 0700 and the file 0600.
- Windows: the `keys` folder's ACL is set, before any key is written into it, to the current user
  alone with no inherited entries, so a file made in it is never readable by anyone else even for
  a moment; the file's own ACL is then set the same way, for one that was there already. `icacls`
  and `whoami` are in System32 on every Windows, and need nothing installed.
"""

from __future__ import annotations

import json
import os
import re
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from agentloop import config
from agentloop.definition import Unreachable

# What a refusal of a pasted key says it is about.
WHERE = "the pasted key"


def folder() -> Path:
    """Where the app keeps the keys it is given: `keys/` in its config folder."""
    return config.paths().config / "keys"


def clean(key: str) -> str:
    """The key as pasted, without the spaces and line ends around it. Refuses an empty one, or one
    of several lines - something else pasted by mistake."""
    key = (key or "").strip()
    if not key:
        raise config.ConfigError(WHERE, "key", "empty: paste the agents' key Overseer's page made")
    if any(c in key for c in "\r\n\t ") or len(key) > 4096:
        raise config.ConfigError(WHERE, "key", "not a key: one line, no spaces")
    return key


def check(server: str, key: str, timeout: float = 30.0) -> str:
    """The account the key signs in as on the server. Raises ConfigError when the server refuses
    it, and Unreachable when it cannot be asked; neither text holds the key or the address."""
    request = urllib.request.Request(f"{server.rstrip('/')}/account/me",
                                     headers={"Authorization": f"Bearer {key}", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            body = answer.read()
    except urllib.error.HTTPError as error:
        if error.code in (401, 403):
            raise config.ConfigError(WHERE, "key", f"the server refused it ({error.code})") from None
        raise Unreachable(f"the server answered {error.code}") from None
    except (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, ValueError) as error:
        reason = getattr(error, "reason", error)
        raise Unreachable(f"the server could not be reached ({type(reason).__name__})") from None
    try:
        account = json.loads(body.decode("utf-8"))["username"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        raise config.ConfigError(WHERE, "key", "the server's answer names no account") from None
    if not isinstance(account, str) or not account:
        raise config.ConfigError(WHERE, "key", "the server's answer names no account")
    return account


def file_name(server: str, account: str) -> str:
    """`<host>-<account>.key`: the host with its port, if any, and the account, in characters
    every system takes in a file name."""
    parts = urllib.parse.urlsplit(server)
    host = parts.hostname or "server"
    if parts.port:
        host += f"_{parts.port}"
    safe = lambda text: re.sub(r"[^A-Za-z0-9._-]", "_", text).strip(".") or "_"
    return f"{safe(host)}-{safe(account)}.key"


def server_for(settings: config.Settings, address: str, worker: str) -> str:
    """The server a key pasted beside a tracker's address is for: the one the address names, else
    the worker's - as the loop added from it would reach its tracker."""
    server, _ = config.parse_address(address)
    if server:
        return server
    found = settings.worker(worker)
    theirs = (found.overseer if found else None) or {}
    if not theirs.get("server"):
        raise config.ConfigError(WHERE, "address", "names no server, and the worker reaches none: "
                                 "give Overseer's whole address of the tracker")
    return str(theirs["server"])


def save(server: str, key: str, timeout: float = 30.0) -> tuple[str, Path]:
    """The key checked against the server, then written to its file: (account, path)."""
    key = clean(key)
    server = (server or "").strip().rstrip("/")
    if not server:
        raise config.ConfigError(WHERE, "server", "the server the key is for")
    account = check(server, key, timeout)
    path = folder() / file_name(server, account)
    write_private(path, key + "\n")
    return account, path


def write_private(path: Path, text: str) -> None:
    """`text` written to `path`, readable and writable by the current user alone."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        _private_windows(path.parent, folder=True)
        path.write_text(text, encoding="utf-8")
        try:
            _private_windows(path, folder=False)
        except Exception:
            # A key left where others might read it is worse than none.
            path.unlink(missing_ok=True)
            raise
        return
    path.parent.chmod(0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    # An existing file keeps its old mode through os.open: set it before the key goes in.
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as out:
        out.write(text)


class PrivacyError(Exception):
    """The file could not be made private. Its text names no path."""


def _windows_sid() -> str:
    """The current user's SID, as `whoami /user` gives it."""
    done = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True,
                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    match = re.search(r"S-1-[0-9-]+", done.stdout or "")
    if done.returncode != 0 or not match:
        raise PrivacyError("whoami named no user")
    return match.group(0)


def _private_windows(path: Path, folder: bool) -> None:
    """The path's ACL: full control for the current user alone, nothing inherited. A folder's
    entry is inherited by what is made in it."""
    me = _windows_sid()
    flags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
    grant = "(OI)(CI)F" if folder else "F"
    done = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{me}:{grant}"],
                          capture_output=True, text=True, creationflags=flags)
    if done.returncode != 0:
        raise PrivacyError(f"icacls failed ({done.returncode})")
    # Entries granted to others directly, not inherited, survive /inheritance:r: remove each.
    for other in set(windows_sids(path)) - {me}:
        subprocess.run(["icacls", str(path), "/remove:g", f"*{other}"], capture_output=True, text=True, creationflags=flags)
    if set(windows_sids(path)) != {me}:
        raise PrivacyError("the ACL names others")


def windows_sids(path: Path) -> list[str]:
    """The SIDs the entries of the path's ACL name. Read through the Win32 API with ctypes, as
    icacls prints account names, in the machine's language."""
    import ctypes
    from ctypes import wintypes

    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi.GetNamedSecurityInfoW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p,
                                             ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                             ctypes.POINTER(ctypes.c_void_p)]
    advapi.GetNamedSecurityInfoW.restype = wintypes.DWORD
    advapi.GetAclInformation.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.c_int]
    advapi.GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p

    class AclSizeInformation(ctypes.Structure):
        _fields_ = [("AceCount", wintypes.DWORD), ("AclBytesInUse", wintypes.DWORD), ("AclBytesFree", wintypes.DWORD)]

    SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, ACL_SIZE_INFORMATION_CLASS = 1, 4, 2
    # An ACCESS_ALLOWED_ACE or ACCESS_DENIED_ACE: a 4-byte header, a 4-byte mask, then the SID.
    SID_OFFSET = 8
    dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
    status = advapi.GetNamedSecurityInfoW(str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None,
                                          ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if status != 0:
        raise PrivacyError(f"the ACL could not be read ({status})")
    try:
        if not dacl.value:
            # No DACL at all lets everyone in.
            return ["S-1-1-0"]
        info = AclSizeInformation()
        if not advapi.GetAclInformation(dacl, ctypes.byref(info), ctypes.sizeof(info), ACL_SIZE_INFORMATION_CLASS):
            raise PrivacyError("the ACL could not be read")
        sids = []
        for index in range(info.AceCount):
            ace = ctypes.c_void_p()
            text = ctypes.c_void_p()
            if not advapi.GetAce(dacl, index, ctypes.byref(ace)):
                raise PrivacyError("the ACL could not be read")
            if not advapi.ConvertSidToStringSidW(ace.value + SID_OFFSET, ctypes.byref(text)):
                raise PrivacyError("the ACL could not be read")
            sids.append(ctypes.wstring_at(text.value))
            kernel.LocalFree(text)
        return sids
    finally:
        kernel.LocalFree(descriptor)
