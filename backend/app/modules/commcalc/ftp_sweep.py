"""Generic FTP-pull I/O (Theme 6). Pure FTP — no app/DB deps (the router does the routing to parsers
+ the processed-tracking, so there's no circular import). Supports plain FTP and explicit FTP_TLS,
passive or active. Files are matched against fnmatch glob patterns.
"""
import fnmatch
import ftplib
import io


def _connect(cfg):
    host = (cfg.get("host") or "").strip()
    if not host:
        raise ValueError("FTP host not configured")
    port = int(cfg.get("port") or 21)
    user = cfg.get("username") or "anonymous"
    pw = cfg.get("password") or ""
    if cfg.get("use_tls"):
        ftp = ftplib.FTP_TLS()
        ftp.connect(host, port, timeout=30)
        ftp.login(user, pw)
        ftp.prot_p()
    else:
        ftp = ftplib.FTP()
        ftp.connect(host, port, timeout=30)
        ftp.login(user, pw)
    ftp.set_pasv(bool(cfg.get("passive", True)))
    remote_dir = (cfg.get("remote_dir") or "/").strip()
    if remote_dir and remote_dir != "/":
        ftp.cwd(remote_dir)
    return ftp


def _list_with_size(ftp):
    """List the current dir as [{name, size}]. Tries SIZE; falls back to MLSD/NLST."""
    out = []
    try:
        # MLSD gives structured facts incl. size when supported
        for name, facts in ftp.mlsd():
            if facts.get("type") in (None, "file"):
                out.append({"name": name, "size": int(facts.get("size") or 0)})
        if out:
            return out
    except Exception:
        pass
    for name in ftp.nlst():
        size = 0
        try:
            size = ftp.size(name) or 0
        except Exception:
            pass
        out.append({"name": name, "size": size})
    return out


def list_files(cfg):
    """Connect and return [{name, size}] in the configured directory (for the test/preview)."""
    ftp = _connect(cfg)
    try:
        return _list_with_size(ftp)
    finally:
        try:
            ftp.quit()
        except Exception:
            pass


def match_upload_type(filename, patterns):
    """Return the upload_type whose glob matches this filename (first match wins), else None.
    Case-INSENSITIVE: fnmatch is case-sensitive on Linux, so '*Inventory*Aging*' would silently miss
    'inventory aging.csv' — the exact 'attachment is there but never imports' failure. Lowercase both."""
    fn = (filename or "").lower()
    for p in patterns or []:
        pat = (p.get("pattern") or "").strip().lower()
        if pat and fnmatch.fnmatch(fn, pat):
            return p.get("upload_type")
    return None


def fetch_new_files(cfg, already):
    """Download every file in the dir that matches a configured pattern and isn't already processed.
    `already` is a set of (filename, size). Returns [{name, size, upload_type, bytes}]."""
    patterns = cfg.get("patterns") or []
    ftp = _connect(cfg)
    out = []
    try:
        for f in _list_with_size(ftp):
            name, size = f["name"], f["size"]
            ut = match_upload_type(name, patterns)
            if not ut:
                continue
            if (name, size) in already:
                continue
            buf = io.BytesIO()
            try:
                ftp.retrbinary(f"RETR {name}", buf.write)
            except Exception as e:
                out.append({"name": name, "size": size, "upload_type": ut, "bytes": None, "error": str(e)})
                continue
            out.append({"name": name, "size": size, "upload_type": ut, "bytes": buf.getvalue()})
    finally:
        try:
            ftp.quit()
        except Exception:
            pass
    return out
