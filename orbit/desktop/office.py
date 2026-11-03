"""Find the file behind a WPS or Office window, and edit the copy that is open."""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

try:
    import winreg
except ImportError:
    winreg = None

from orbit.desktop.files import SKIP_DIRS

_NAME = re.compile(r"([^\\/:*?\"<>|\r\n]+?\.(?:docx|docm|doc|xlsx|xls|pptx|ppt))", re.I)
_PATH = re.compile(
    r"[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]+\.(?:docx|docm|doc|xlsx|xls|pptx|ppt)",
    re.I,
)
_WALK_SKIP = SKIP_DIRS | {"AppData", "Application Data", "Local Settings", "Cookies"}

_LIVE_SCRIPT = r"""
param([string]$PayloadPath, [string]$ResultPath)
$ErrorActionPreference = "Stop"
function Write-Result([hashtable]$Object) {
  $json = $Object | ConvertTo-Json -Compress -Depth 3
  [IO.File]::WriteAllText($ResultPath, $json, (New-Object System.Text.UTF8Encoding $false))
}
try {
  Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public class OrbitActiveOffice {
  [DllImport("oleaut32.dll", PreserveSig=false)]
  private static extern void GetActiveObject(ref Guid rclsid, IntPtr reserved, [MarshalAs(UnmanagedType.IUnknown)] out object ppunk);
  public static object Get(string progId) {
    var type = Type.GetTypeFromProgID(progId);
    if (type == null) throw new Exception("missing");
    Guid id = type.GUID;
    object obj;
    GetActiveObject(ref id, IntPtr.Zero, out obj);
    return obj;
  }
}
"@
  $payload = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
  $progids = @(
    "kwps.Application", "KWPS.Application", "wps.Application", "Word.Application",
    "ket.Application", "Excel.Application", "kwpp.Application", "PowerPoint.Application"
  )
  $target = [string]$payload.path
  $action = [string]$payload.action
  $apps = @()
  foreach ($id in $progids) {
    try { $apps += [OrbitActiveOffice]::Get($id) } catch {}
  }
  if (-not $apps.Count) { Write-Result @{ status = "noapp" }; exit 0 }
  $docs = @()
  foreach ($app in $apps) {
    try { $collection = $app.Documents } catch { continue }
    $count = 0
    try { $count = [int]$collection.Count } catch { continue }
    for ($i = 1; $i -le $count; $i++) {
      try { $docs += $collection.Item($i) } catch {}
    }
  }
  if ($action -eq "locate") {
    $want = [IO.Path]::GetFileName($target).ToLowerInvariant()
    foreach ($doc in $docs) {
      $docName = ""
      try { $docName = [string]$doc.Name } catch { continue }
      if ($docName.ToLowerInvariant() -ne $want) { continue }
      $full = ""
      try { $full = [string]$doc.FullName } catch { continue }
      if ($full) { Write-Result @{ status = "path"; path = $full }; exit 0 }
    }
    if ($docs.Count -eq 1) {
      $full = ""
      try { $full = [string]$docs[0].FullName } catch {}
      if ($full) { Write-Result @{ status = "path"; path = $full; only = $true }; exit 0 }
    }
    Write-Result @{ status = "noapp" }
    exit 0
  }
  $exact = $null
  $named = $null
  foreach ($doc in $docs) {
    $full = ""
    try { $full = [string]$doc.FullName } catch { continue }
    if (-not $full) { continue }
    try {
      $left = [IO.Path]::GetFullPath($full).TrimEnd("\").ToLowerInvariant()
      $right = [IO.Path]::GetFullPath($target).TrimEnd("\").ToLowerInvariant()
    } catch { continue }
    if ($left -eq $right) { $exact = $doc; break }
    if ([IO.Path]::GetFileName($left) -eq [IO.Path]::GetFileName($right)) { $named = $doc }
  }
  $chosen = $exact
  if (-not $chosen) { $chosen = $named }
  if (-not $chosen -and $docs.Count -eq 1) { $chosen = $docs[0] }
  if (-not $chosen) { Write-Result @{ status = "noapp" }; exit 0 }
  if ($action -eq "read") {
    $text = ""
    try { $text = [string]$chosen.Content.Text } catch { Write-Result @{ status = "noapp" }; exit 0 }
    $text = $text -replace "`r", "`n"
    if ($text.Length -gt 8000) { $text = $text.Substring(0, 8000) }
    Write-Result @{ status = "text"; text = $text }
    exit 0
  }
  if ($action -eq "paragraph") {
    $want = 0
    try { $want = [int]$payload.index } catch { Write-Result @{ status = "nomatch" }; exit 0 }
    $expect = ([string]$payload.old).Trim()
    $seen = 0
    foreach ($para in @($chosen.Paragraphs)) {
      $raw = ""
      try { $raw = [string]$para.Range.Text } catch { continue }
      $text = ($raw -replace "[`r`n`a]+$", "").Trim()
      if (-not $text) { continue }
      $seen++
      if ($seen -ne $want) { continue }
      if ($expect -and $text -ne $expect) { Write-Result @{ status = "nomatch" }; exit 0 }
      $range = $para.Range.Duplicate
      $null = $range.MoveEnd(1, -1)
      $range.Text = [string]$payload.new
      try { $chosen.Save() } catch {}
      Write-Result @{ status = "replaced" }
      exit 0
    }
    Write-Result @{ status = "nomatch" }
    exit 0
  }
  $old = [string]$payload.old
  $new = [string]$payload.new
  $find = $chosen.Content.Find
  $find.ClearFormatting()
  $find.Replacement.ClearFormatting()
  $find.Text = $old
  $find.Replacement.Text = $new
  $find.Forward = $true
  $find.Wrap = 1
  $find.Format = $false
  $find.MatchCase = $false
  $ok = $find.Execute($old, [bool]$false, [bool]$false, [bool]$false, [bool]$false, [bool]$false, [bool]$true, [int]1, [bool]$false, $new, [int]1)
  if ($ok) {
    try { $chosen.Save() } catch {}
    Write-Result @{ status = "replaced" }
  } else { Write-Result @{ status = "nomatch" } }
} catch {
  Write-Result @{ status = "error"; message = [string]$_.Exception.Message }
  exit 1
}
"""


def document_name(title):
    text = (title or "").replace("*", " ")
    text = re.sub(r"\[[^\]]{0,24}\]", " ", text)
    match = _NAME.search(text)
    return match.group(1).strip() if match else None


def _blocked_roots():
    roots = []
    for key in ("WINDIR", "SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432"):
        value = os.environ.get(key)
        if not value:
            continue
        try:
            roots.append(Path(value).resolve())
        except OSError:
            continue
    return roots


def is_blocked_dir(path):
    """Windows, System32, and Program Files are never a document workspace."""
    try:
        resolved = Path(path).resolve()
    except OSError:
        return True
    for base in _blocked_roots():
        if resolved == base or base in resolved.parents:
            return True
    return False


def _push(found, path, filename):
    try:
        candidate = Path(path)
        if not candidate.is_file() or candidate.name.lower() != filename.lower():
            return
        if is_blocked_dir(candidate):
            return
        resolved = candidate.resolve()
    except OSError:
        return
    key = os.path.normcase(str(resolved))
    if key in found:
        return
    found[key] = resolved


def _command_paths(command, filename):
    found = {}
    for match in _PATH.finditer(command or ""):
        _push(found, match.group(0).strip('"'), filename)
    return list(found.values())


def _paths_in_blob(blob, filename):
    found = []
    texts = []
    if isinstance(blob, str):
        texts.append(blob)
    else:
        texts.append(blob.decode("utf-8", "ignore"))
        texts.append(blob.decode("utf-16le", "ignore"))
    needle = filename.lower()
    for text in texts:
        if needle not in text.lower():
            continue
        for match in _PATH.finditer(text):
            if match.group(0).lower().endswith(needle):
                found.append(match.group(0))
    return found


def _registry_paths(filename):
    found = []
    if winreg is None:
        return found
    roots = (
        r"Software\Kingsoft",
        r"Software\kingsoft",
        r"Software\Microsoft\Office",
    )
    needle = filename.lower()

    def walk(root, path, depth):
        try:
            key = winreg.OpenKey(root, path)
        except OSError:
            return
        try:
            index = 0
            while index < 80:
                try:
                    _name, value, _kind = winreg.EnumValue(key, index)
                except OSError:
                    break
                index += 1
                if isinstance(value, str) and needle in value.lower():
                    found.extend(_paths_in_blob(value, filename))
            if depth <= 0:
                return
            index = 0
            while index < 40:
                try:
                    child = winreg.EnumKey(key, index)
                except OSError:
                    break
                index += 1
                walk(root, path + "\\" + child, depth - 1)
        finally:
            winreg.CloseKey(key)

    for path in roots:
        walk(winreg.HKEY_CURRENT_USER, path, 3)
    return found


def _recent_files(filename):
    found = []
    appdata = Path(os.environ.get("APPDATA", ""))
    recent = appdata / "Microsoft" / "Windows" / "Recent"
    if recent.is_dir():
        for link in recent.glob(filename + "*.lnk"):
            try:
                if link.stat().st_size > 1_000_000:
                    continue
                found.extend(_paths_in_blob(link.read_bytes(), filename))
            except OSError:
                continue
    for base in (appdata / "kingsoft", appdata / "Kingsoft", appdata / "Microsoft" / "Office"):
        if not base.is_dir():
            continue
        deadline = time.monotonic() + 1.5
        for dirpath, dirnames, filenames in os.walk(base):
            if time.monotonic() > deadline:
                break
            dirnames[:] = [item for item in dirnames if item not in _WALK_SKIP and not item.startswith(".")]
            if len(Path(dirpath).relative_to(base).parts) > 5:
                dirnames.clear()
                continue
            for name in filenames:
                file = Path(dirpath) / name
                try:
                    if file.stat().st_size > 2_000_000:
                        continue
                    blob = file.read_bytes()
                except OSError:
                    continue
                if filename.lower() not in blob.decode("utf-8", "ignore").lower() and filename.lower() not in blob.decode("utf-16le", "ignore").lower():
                    continue
                found.extend(_paths_in_blob(blob, filename))
    found.extend(_registry_paths(filename))
    return found


def _user_folders():
    folders = []
    home = Path.home()
    folders.extend([
        home / "Desktop", home / "Documents", home / "Downloads", home / "OneDrive",
        home / "WPS Cloud Files",
    ])
    key = None
    if winreg is not None:
        try:
            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders",
            )
        except OSError:
            key = None
    if key is not None:
        try:
            for name in ("Desktop", "Personal", "{374DE290-123F-4565-9164-39C4925E467B}"):
                try:
                    value, _kind = winreg.QueryValueEx(key, name)
                except OSError:
                    continue
                folders.append(Path(os.path.expandvars(value)))
        finally:
            winreg.CloseKey(key)
    unique = []
    seen = set()
    for folder in folders:
        try:
            resolved = folder.resolve()
        except OSError:
            continue
        key = os.path.normcase(str(resolved))
        if key in seen or not resolved.is_dir() or is_blocked_dir(resolved):
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def _cloud_roots():
    home = Path.home()
    roots = [home / "WPS Cloud Files", home / "WPSDrive"]
    return [root for root in roots if root.is_dir() and not is_blocked_dir(root)]


def _search_named(filename, roots=None, seconds=5):
    found = []
    deadline = time.monotonic() + seconds
    for root in (roots if roots is not None else _user_folders()):
        allow_dot = "wps" in root.name.lower()
        for dirpath, dirnames, filenames in os.walk(root):
            if time.monotonic() > deadline or len(found) >= 8:
                return found
            dirnames[:] = [
                item for item in dirnames
                if item not in _WALK_SKIP and (allow_dot or not item.startswith("."))
            ]
            if len(Path(dirpath).relative_to(root).parts) > 6:
                dirnames.clear()
                continue
            for name in filenames:
                if name.lower() == filename.lower():
                    found.append(Path(dirpath) / name)
    return found


def _newest(paths):
    def stamp(path):
        try:
            return path.stat().st_mtime
        except OSError:
            return 0

    return max(paths, key=stamp)


def locate_document(title, command=""):
    """Resolve the open document. Never returns a Windows or Program Files path."""
    filename = document_name(title)
    if not filename:
        return None
    live = active_document_path(filename)
    if live:
        return live
    cloud_hits = []
    for path in _search_named(filename, roots=_cloud_roots(), seconds=4):
        try:
            if path.is_file() and not is_blocked_dir(path):
                cloud_hits.append(path.resolve())
        except OSError:
            continue
    if cloud_hits:
        return cloud_hits[0] if len(cloud_hits) == 1 else _newest(cloud_hits)
    from_command = _command_paths(command, filename)
    if from_command:
        return from_command[0] if len(from_command) == 1 else _newest(from_command)
    found = {}
    for path in _recent_files(filename):
        _push(found, path, filename)
    if found:
        values = list(found.values())
        return values[0] if len(values) == 1 else _newest(values)
    searched = []
    for path in _search_named(filename):
        try:
            if path.is_file() and not is_blocked_dir(path):
                searched.append(path.resolve())
        except OSError:
            continue
    if not searched:
        return None
    return searched[0] if len(searched) == 1 else _newest(searched)


def open_document_path(raw, root, attached):
    from orbit.desktop.files import looks_secret

    if not str(raw or "").strip():
        if not attached:
            raise ValueError("没有指定文档，也没有吸附中的文档。")
        candidate = Path(attached)
    else:
        candidate = Path(str(raw)).expanduser()
        if not candidate.is_absolute():
            candidate = Path(root) / candidate
    try:
        resolved = candidate.resolve()
    except OSError as error:
        raise ValueError("找不到文件：" + str(error)) from error
    if looks_secret(resolved):
        raise ValueError("敏感文件，已拒绝。")
    if is_blocked_dir(resolved):
        raise ValueError("这个路径不在允许范围内。")
    home = Path.home().resolve()
    base = Path(root).resolve()
    attached_path = None
    if attached:
        try:
            attached_path = Path(attached).resolve()
        except OSError:
            attached_path = None
    allowed = (
        resolved == base or base in resolved.parents or resolved == home or home in resolved.parents
        or (attached_path is not None and resolved == attached_path)
    )
    if not allowed:
        raise ValueError("这个路径不在允许范围内。")
    if not resolved.is_file():
        raise ValueError("找不到文件：" + str(resolved))
    return resolved


def _office_call(payload):
    directory = tempfile.mkdtemp(prefix="orbit-office-")
    payload_path = Path(directory) / "payload.json"
    result_path = Path(directory) / "result.json"
    script_path = Path(directory) / "office.ps1"
    try:
        payload_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8-sig")
        script_path.write_text(_LIVE_SCRIPT, encoding="utf-8-sig")
        creation = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        system = os.environ.get("SystemRoot", r"C:\Windows")
        powershell = Path(system) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
        subprocess.run(
            [
                str(powershell if powershell.is_file() else "powershell.exe"),
                "-NoProfile", "-Sta", "-ExecutionPolicy", "Bypass",
                "-File", str(script_path), str(payload_path), str(result_path),
            ],
            capture_output=True, timeout=30, creationflags=creation,
        )
        if not result_path.is_file():
            return {"status": "noapp"}
        return json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError, ValueError):
        return {"status": "noapp"}
    finally:
        for file in (payload_path, result_path, script_path):
            try:
                file.unlink()
            except OSError:
                pass
        try:
            os.rmdir(directory)
        except OSError:
            pass


def active_document_path(filename):
    """Ask the running WPS or Office app which file with this name is open."""
    result = _office_call({"action": "locate", "path": filename, "old": "", "new": ""})
    if result.get("status") != "path":
        return None
    try:
        candidate = Path(str(result.get("path") or ""))
        names_match = candidate.name.lower() == Path(filename).name.lower()
        if candidate.is_file() and not is_blocked_dir(candidate) and (names_match or result.get("only")):
            return candidate.resolve()
    except OSError:
        return None
    return None


def read_open_document(path):
    result = _office_call({"action": "read", "path": str(path), "old": "", "new": ""})
    if result.get("status") != "text":
        return ""
    return str(result.get("text") or "").strip()


def replace_open_paragraph(path, index, old, new):
    """Replace one numbered paragraph in the document WPS or Word has open."""
    result = _office_call({
        "action": "paragraph", "path": str(path), "old": old, "new": new, "index": int(index),
    })
    status = result.get("status")
    if status in ("replaced", "nomatch"):
        return status
    return "noapp"


def replace_open_document(path, old, new):
    result = _office_call({"action": "replace", "path": str(path), "old": old, "new": new})
    status = result.get("status")
    if status in ("replaced", "nomatch"):
        return status
    return "noapp"
