import subprocess
import sys
import os
import shutil
import stat
import contextlib

# glibc malloc arena 제한 설정: glibc는 스레드별 아레나를 유지하며 이를 해제하지 않으므로,
# 백그라운드 OCR 작업 시 메모리 사용량(RSS)이 계속 증가하여 512MB 컨테이너가 OOM으로 종료될 수 있습니다.
# glibc 초기화 전에 설정되어야 하므로 설정되어 있지 않은 경우 재실행합니다.
if sys.platform.startswith("linux") and "MALLOC_ARENA_MAX" not in os.environ:
    os.environ["MALLOC_ARENA_MAX"] = "2"
    os.environ.setdefault("MALLOC_TRIM_THRESHOLD_", "131072")
    try:
        from cogs import bot_startup_display as _startup
        _startup.phase_ok("메모리 할당 최적화 완료")
    except Exception:
        print("  메모리 할당 최적화 완료", flush=True)
    os.execv(sys.executable, [sys.executable, *sys.argv])


def _bootstrap_from_main_branch(zip_url):
    """자체 복구: 소스 zip을 다운로드하고 임시 디렉터리를 통해 설치합니다."""
    import urllib.request, zipfile, io
    print("봇 파일이 없습니다. 최신 소스 코드를 다운로드합니다...")
    try:
        with urllib.request.urlopen(zip_url, timeout=600) as r:
            buf = io.BytesIO(r.read())
    except Exception as e:
        print(f"다운로드 실패: {e}")
        print(f"수동으로 다음 위치에서 소스를 다운로드해주세요:\n  {zip_url}")
        print("모든 압축을 이 폴더에 해제한 후 main.py를 다시 실행하세요.")
        sys.exit(1)

    staging = "bot-bootstrap-tmp"
    if os.path.exists(staging):
        shutil.rmtree(staging, ignore_errors=True)
    os.makedirs(staging)
    staging_root = os.path.realpath(staging)

    try:
        with zipfile.ZipFile(buf) as zf:
            for m in zf.namelist():
                parts = m.split("/", 1)
                if len(parts) < 2 or not parts[1]:
                    continue
                target = os.path.realpath(os.path.join(staging, parts[1]))
                if target != staging_root and not target.startswith(staging_root + os.sep):
                    raise RuntimeError(f"zip 파일 항목이 임시 디렉터리를 벗어났습니다: {m}")
                if m.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                else:
                    os.makedirs(os.path.dirname(target) or staging, exist_ok=True)
                    with zf.open(m) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
        # 압축 해제 성공 — 작업 디렉터리로 복사
        for item in os.listdir(staging):
            src = os.path.join(staging, item)
            if os.path.isdir(src):
                shutil.copytree(src, item, dirs_exist_ok=True)
            else:
                shutil.copy2(src, item)
    except Exception as e:
        print(f"압축 해제 실패: {e}")
        print("기존 파일은 변경되지 않았습니다. main.py를 다시 실행하여 재시도하세요.")
        sys.exit(1)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

try:
    from cogs import bot_startup_display as startup
    from cogs.bot_restart import is_container, restart_process
except ImportError:
    # cogs/ 폴더가 없는 경우 실행
    _bootstrap_from_main_branch(os.environ.get(
        "BOT_BOOTSTRAP_URL",
        "https://github.com/whiteout-project/bot/archive/refs/heads/main.zip",
    ))
    print("다운로드 완료. 재시작 중...")
    if sys.platform == "win32":
        sys.exit(0)
    os.execv(sys.executable, [sys.executable] + sys.argv)

# 파이썬 버전 확인
MIN_PYTHON = (3, 11)

if sys.version_info < MIN_PYTHON:
    startup.python_too_old(f"{MIN_PYTHON[0]}.{MIN_PYTHON[1]}", f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    sys.exit(1)

def is_ci_environment() -> bool:
    """CI 환경에서 실행 중인지 확인합니다."""
    ci_indicators = [
        'CI', 'CONTINUOUS_INTEGRATION', 'GITHUB_ACTIONS', 
        'JENKINS_URL', 'TRAVIS', 'CIRCLECI', 'GITLAB_CI'
    ]
    return any(os.getenv(indicator) for indicator in ci_indicators)

def break_system_packages() -> bool:
    """시스템 패키지 설치 옵션 사용 여부를 확인합니다."""
    return "--break-system-packages" in sys.argv

def break_system_packages_arg() -> bool:
    return break_system_packages() and not should_skip_venv()

def _pip_env():
    """pip 임시 디렉터리 환경 설정"""
    env = os.environ.copy()
    tmp = os.path.join(os.getcwd(), ".pip-tmp")
    try:
        os.makedirs(tmp, exist_ok=True)
        env["TMPDIR"] = tmp   # POSIX
        env["TEMP"] = tmp     # Windows
        env["TMP"] = tmp
    except Exception:
        pass
    return env

def should_skip_venv() -> bool:
    """가상 환경(venv) 건너뛰기 여부를 확인합니다."""
    
    if ("--no-venv" in sys.argv) and (sys.platform.startswith("linux")) and (not is_container()) and (not is_ci_environment()) and (not break_system_packages()):
        startup.error_box(
            "가상 환경이 필요합니다",
            "Linux 환경에서는 시스템 패키지 옵션 없이 가상 환경 외 실행이 불가능합니다.",
            "위험 요소를 확인하셨다면 --break-system-packages 인자를 추가하세요."
        )
        sys.exit(1)
    
    return '--no-venv' in sys.argv or is_container() or is_ci_environment()

# 가상 환경(venv) 설정
if sys.prefix == sys.base_prefix and not should_skip_venv():
    venv_path = "bot_venv"

    if sys.platform == "win32":
        venv_python_name = os.path.join(venv_path, "Scripts", "python.exe")
        activate_script = os.path.join(venv_path, "Scripts", "activate.bat")
    else:
        venv_python_name = os.path.join(venv_path, "bin", "python")
        activate_script = os.path.join(venv_path, "bin", "activate")

    if not os.path.exists(venv_path):
        try:
            startup.phase_start("가상 환경을 생성하는 중입니다")
            subprocess.check_call([sys.executable, "-m", "venv", venv_path], timeout=300)

            try:
                subprocess.run([venv_python_name, "-m", "pip", "install", "--upgrade",
                                "setuptools", "wheel"], timeout=300,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            if sys.platform == "win32":
                startup.venv_instructions(venv_python_name, sys.platform)
                sys.exit(0)
            else:
                startup.venv_instructions(venv_python_name, sys.platform)
                venv_python_executable = os.path.join(venv_path, "bin", "python")
                os.execv(venv_python_executable, [venv_python_executable] + sys.argv)

        except Exception as e:
            startup.error_box("가상 환경 생성 실패", str(e), "python -m venv bot_venv")
            sys.exit(1)
    else:
        if sys.platform == "win32":
            startup.venv_exists_instructions(venv_python_name, sys.platform)
            sys.exit(0)
        elif '--no-venv' in sys.argv:
            pass
        else:
            venv_python_executable = os.path.join(venv_path, "bin", "python")
            if os.path.exists(venv_python_executable):
                os.execv(venv_python_executable, [venv_python_executable] + sys.argv)
            else:
                startup.error_box("가상 환경 손상됨", "bot_venv 폴더를 삭제한 후 다시 실행해 주세요.")
                sys.exit(1)

try:
    import requests
except ImportError:
    try:
        cmd = [sys.executable, "-m", "pip", "install", "requests"]

        if break_system_packages_arg():
            cmd.append("--break-system-packages")

        subprocess.check_call(cmd, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        import requests
    except Exception as e:
        startup.error_box("의존성 패키지 오류", f"requests 패키지 설치 실패: {e}", "pip install requests")
        sys.exit(1)

def remove_readonly(func, path, _):
    os.chmod(path, stat.S_IWRITE)
    func(path)

def safe_remove(path, is_dir=None):
    if not os.path.exists(path):
        return True
    
    if is_dir is None:
        is_dir = os.path.isdir(path)
    
    try:
        if is_dir:
            if sys.platform == "win32":
                if sys.version_info >= (3, 12):
                    shutil.rmtree(path, onexc=remove_readonly)
                else:
                    shutil.rmtree(path, onerror=remove_readonly)
            else:
                shutil.rmtree(path)
        else:
            try:
                os.remove(path)
            except PermissionError:
                if sys.platform == "win32":
                    os.chmod(path, stat.S_IWRITE)
                    os.remove(path)
                else:
                    raise
        
        return True
        
    except PermissionError:
        print(f"경고: 접근이 거부되었습니다. '{path}' 항목을 삭제할 수 없습니다.\n권한을 확인하거나 해당 {'디렉터리' if is_dir else '파일'}이 사용 중인지 확인하세요.")
    except OSError as e:
        print(f"경고: '{path}' 항목을 삭제할 수 없습니다: {e}")
    
    return False

def _requirement_name(spec):
    for sep in ("==", ">=", "<=", "~=", "!="):
        spec = spec.split(sep)[0]
    return spec


OPTIONAL_PACKAGES = {"onnxruntime", "rapidocr"}


def _pip_install_one(package, *, retries=1):
    import time
    cmd = [sys.executable, "-m", "pip", "install", package, "--no-cache-dir"]
    if break_system_packages_arg():
        cmd.append("--break-system-packages")
    tail = []
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(3)
        try:
            result = subprocess.run(cmd, timeout=1200, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=_pip_env())
            if result.returncode == 0:
                return True, []
            tail = (result.stdout or "").strip().splitlines()[-15:]
        except Exception as e:
            tail = [str(e)]
    return False, tail


def _install_requirements_individually(requirements_txt_path):
    try:
        with open(requirements_txt_path, "r") as f:
            packages = [ln.strip() for ln in f
                        if ln.strip() and not ln.strip().startswith("#")]
    except OSError as e:
        startup.phase_fail("업데이트 실패", details=[f"requirements 목록을 읽을 수 없습니다: {e}"],
                           fix="pip install -r requirements.txt")
        return False

    for package in packages:
        ok, tail = _pip_install_one(package)
        if ok:
            continue
        if _requirement_name(package).lower() in OPTIONAL_PACKAGES:
            startup.warn(f"{_requirement_name(package)} 패키지를 설치할 수 없습니다 - 스크린샷 인식(OCR) "
                         f"기능이 비활성화됩니다. 다른 기능은 정상 작동합니다.")
            continue
        startup.phase_fail("업데이트 실패",
                           details=[f"{package} 설치 실패:", *tail],
                           fix="pip install -r requirements.txt")
        return False
    return True

def calculate_file_hash(filepath):
    import hashlib
    if not os.path.exists(filepath):
        return None
    
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except Exception:
        return None

def cleanup_removed_packages():
    if not (os.path.exists("requirements.old") and os.path.exists("requirements.txt")):
        if os.path.exists("requirements.old"):
            safe_remove("requirements.old", is_dir=False)
        return

    def _read_pkgs(path):
        result = set()
        try:
            with open(path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        result.add(_requirement_name(line).strip().lower())
        except Exception:
            pass
        return result

    PROTECTED = {"setuptools", "pip", "wheel"}
    removed = _read_pkgs("requirements.old") - _read_pkgs("requirements.txt") - PROTECTED
    if removed:
        debug = "--verbose" in sys.argv or "--debug" in sys.argv
        print(f"제거 대상 패키지 {len(removed)}개 발견: {', '.join(removed)}")
        for package in removed:
            cmd = [sys.executable, "-m", "pip", "uninstall", "-y", package]
            if break_system_packages_arg():
                cmd.append("--break-system-packages")
            try:
                if debug:
                    subprocess.check_call(cmd, timeout=300)
                else:
                    subprocess.check_call(cmd, timeout=300, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

    safe_remove("requirements.old", is_dir=False)

UPDATE_SOURCES = [
    {
        "name": "GitHub",
        "api_url": "https://api.github.com/repos/whiteout-project/bot/releases/latest",
        "primary": True
    },
    {
        "name": "GitLab",
        "api_url": "https://gitlab.whiteout-bot.com/api/v4/projects/1/releases",
        "project_id": 1,
        "primary": False
    }
]

def _parse_version(tag):
    if not tag:
        return None
    try:
        parts = [int(n) for n in tag.strip().lstrip("vV").split(".")[:3]]
    except ValueError:
        return None
    if not parts:
        return None
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)

def _is_newer_version(candidate, current):
    c, cur = _parse_version(candidate), _parse_version(current)
    if c is None or cur is None:
        return True
    return c > cur

def get_latest_release_info(beta_mode=False):
    for source in UPDATE_SOURCES:
        try:
            startup.phase_start(f"업데이트 확인 중 ({source['name']})")
            
            if source['name'] == "GitHub":
                if beta_mode:
                    repo_name = source['api_url'].split('/repos/')[1].split('/releases')[0]
                    branch_url = f"https://api.github.com/repos/{repo_name}/branches/main"
                    response = requests.get(branch_url, timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        commit_sha = data['commit']['sha'][:7]
                        return {
                            "tag_name": f"beta-{commit_sha}",
                            "body": f"main 브랜치의 최신 개발 버전 (커밋: {commit_sha})",
                            "download_url": f"https://github.com/{repo_name}/archive/refs/heads/main.zip",
                            "source": f"{source['name']} (베타)"
                        }
                else:
                    response = requests.get(source['api_url'], timeout=30)
                    if response.status_code == 200:
                        data = response.json()
                        repo_name = source['api_url'].split('/repos/')[1].split('/releases')[0]
                        download_url = f"https://github.com/{repo_name}/archive/refs/tags/{data['tag_name']}.zip"
                        return {
                            "tag_name": data["tag_name"],
                            "body": data["body"],
                            "download_url": download_url,
                            "source": source['name']
                        }
                    
            elif source['name'] == "GitLab":
                response = requests.get(source['api_url'], timeout=30)
                if response.status_code == 200:
                    releases = response.json()
                    if releases:
                        latest = releases[0]
                        tag_name = latest['tag_name']
                        download_url = f"https://gitlab.whiteout-bot.com/whiteout-project/bot/-/archive/{tag_name}/bot-{tag_name}.zip"
                        return {
                            "tag_name": tag_name,
                            "body": latest.get("description", "릴리스 노트가 없습니다."),
                            "download_url": download_url,
                            "source": source['name']
                        }
            
        except requests.exceptions.RequestException:
            continue
        except Exception:
            continue

    startup.phase_fail("업데이트 확인 실패 (모든 서버에 연결할 수 없음)")
    return None

def download_requirements_from_release(beta_mode=False):
    if os.path.exists("requirements.txt"):
        return True
    
    release_info = get_latest_release_info(beta_mode=beta_mode)
    if not release_info:
        return False
    
    tag = release_info["tag_name"]
    source_name = release_info.get("source", "Unknown")
    
    if source_name == "GitHub" or "GitHub" in source_name:
        if beta_mode:
            raw_url = "https://raw.githubusercontent.com/whiteout-project/bot/main/requirements.txt"
        else:
            raw_url = f"https://raw.githubusercontent.com/whiteout-project/bot/refs/tags/{tag}/requirements.txt"
    elif source_name == "GitLab":
        if beta_mode:
            raw_url = "https://gitlab.whiteout-bot.com/whiteout-project/bot/-/raw/main/requirements.txt"
        else:
            raw_url = f"https://gitlab.whiteout-bot.com/whiteout-project/bot/-/raw/{tag}/requirements.txt"
    else:
        return False

    try:
        response = requests.get(raw_url, timeout=30)

        if response.status_code == 200:
            with open("requirements.txt", "w") as f:
                f.write(response.text)
            return True
        else:
            return False

    except Exception:
        return False

def check_and_install_requirements():
    if not os.path.exists("requirements.txt"):
        return False

    with open("requirements.txt", "r") as f:
        requirements = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    
    missing_packages = []
    
    for requirement in requirements:
        package_name = _requirement_name(requirement)

        try:
            if package_name == "discord.py":
                import discord
            elif package_name == "aiohttp-socks":
                import aiohttp_socks
            elif package_name == "python-dotenv":
                import dotenv
            elif package_name == "python-bidi":
                import bidi
            elif package_name == "arabic-reshaper":
                import arabic_reshaper
            elif package_name.lower() == "pillow":
                import PIL
            elif package_name.lower() == "numpy":
                import numpy
            else:
                __import__(package_name)

        except ImportError:
            missing_packages.append(requirement)

    if missing_packages:
        startup.phase_start("누락된 필수 패키지 설치 중")
        for package in missing_packages:
            ok, tail = _pip_install_one(package)
            if ok:
                continue
            if _requirement_name(package).lower() in OPTIONAL_PACKAGES:
                startup.warn(f"{_requirement_name(package)} 패키지를 설치할 수 없습니다 - 스크린샷 인식(OCR) "
                             f"기능이 비활성화됩니다. 다른 기능은 정상 작동합니다.")
                continue
            startup.phase_fail("의존성 패키지 확인 실패",
                               details=[f"{package} 설치 실패:", *tail],
                               fix="pip install -r requirements.txt")
            return False

    startup.phase_ok("필수 패키지 준비 완료")
    return True

def ensure_opencv_headless():
    try:
        from importlib.metadata import distributions
        installed = {(d.metadata.get("Name") or "").lower().replace("_", "-")
                     for d in distributions()}
    except Exception:
        return
    if not ({"opencv-python", "opencv-contrib-python"} & installed):
        return
    startup.phase_start("OpenCV 패키지를 Headless 버전으로 전환하는 중")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "uninstall", "-y",
             "opencv-python", "opencv-contrib-python", "opencv-python-headless"],
            timeout=600, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        cmd = [sys.executable, "-m", "pip", "install", "--no-cache-dir", "opencv-python-headless"]
        if break_system_packages_arg():
            cmd.append("--break-system-packages")
        subprocess.check_call(cmd, timeout=1200, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=_pip_env())
        startup.phase_ok("OpenCV Headless 설정 완료")
    except Exception as e:
        startup.phase_fail(
            "OpenCV Headless 전환 실패", details=[str(e)],
            fix="pip uninstall -y opencv-python opencv-contrib-python && pip install opencv-python-headless",
        )


def setup_dependencies(beta_mode=False):
    startup.phase_start("필수 실행 환경 검사 중")

    if not os.path.exists("requirements.txt"):
        if not download_requirements_from_release(beta_mode=beta_mode):
            startup.phase_fail("의존성 패키지 확인 실패", details=["requirements.txt 파일 다운로드 실패"], fix="https://github.com/whiteout-project/bot/releases 에서 전체 봇 패키지를 다운로드하세요.")
            return False

    if not check_and_install_requirements():
        startup.phase_fail("의존성 패키지 확인 실패", fix="pip install -r requirements.txt")
        return False

    ensure_opencv_headless()
    return True

beta_mode = "--beta" in sys.argv
setup_dependencies(beta_mode=beta_mode)

try:
    from colorama import Fore, Style, init
    import discord
except ImportError as e:
    startup.error_box("모듈 불러오기 실패", f"의존성 설정 후 모듈을 로드할 수 없습니다: {e}", "스크립트를 재실행하거나 직접 실행하세요: pip install -r requirements.txt")
    sys.exit(1)

F = Fore
R = Style.RESET_ALL

import warnings
import aiohttp

def check_vcredist():
    """Windows 환경의 Visual C++ 재배포 가능 패키지 설치 유무 확인"""
    if sys.platform != "win32":
        return True

    try:
        import winreg
        import struct

        is_64bit = struct.calcsize("P") * 8 == 64
        arch = "x64" if is_64bit else "x86"

        key_path = f"SOFTWARE\\Microsoft\\VisualStudio\\14.0\\VC\\Runtimes\\{arch}"

        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path)
            winreg.CloseKey(key)
            return True
        except FileNotFoundError:
            download_url = f"https://aka.ms/vc14/vc_redist.{arch}.exe"
            startup.phase_fail(f"Visual C++ Redistributable ({arch}) 패키지가 없습니다.",
                details=["패키지를 설치하기 전까지 기프트 코드 자동 입력(캡차 해제) 기능이 동작하지 않습니다."],
                fix=f"다음 주소에서 다운로드하세요: {download_url}")
            return False

    except Exception:
        return True

check_vcredist()

warnings.filterwarnings("ignore", category=DeprecationWarning)

init(autoreset=True)

try:
    import ssl
    import certifi

    def _create_ssl_context_with_certifi():
        return ssl.create_default_context(cafile=certifi.where())
    
    original_create_default_https_context = getattr(ssl, "_create_default_https_context", None)

    if original_create_default_https_context is None or \
       original_create_default_https_context is ssl.create_default_context:
        ssl._create_default_https_context = _create_ssl_context_with_certifi
except ImportError:
    pass
except Exception:
    pass

def raise_open_file_limit(floor=1024, target=4096):
    try:
        import resource
    except ImportError:
        return
    try:
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    except Exception:
        return
    if soft == resource.RLIM_INFINITY or soft >= floor:
        return
    ceiling = target if hard == resource.RLIM_INFINITY else min(target, hard)
    new_soft = soft
    for candidate in (ceiling, 2048, 1024):
        if candidate <= soft:
            break
        try:
            resource.setrlimit(resource.RLIMIT_NOFILE, (candidate, hard))
            new_soft = candidate
            break
        except (ValueError, OSError):
            continue
    if new_soft > soft:
        startup.info(f"동시 파일 열기 제한을 확장했습니다: {soft} -> {new_soft}")
    if new_soft < floor:
        startup.warn(
            f"파일 열기 제한 수치가 {new_soft}로 낮아 데이터베이스 읽기 오류가 발생할 수 있습니다. "
            "터미널에서 'ulimit -n 4096' 명령어를 실행한 뒤 봇을 다시 켜세요."
        )


if __name__ == "__main__":
    _proxy_url = None
    if "--proxy" in sys.argv:
        _proxy_idx = sys.argv.index("--proxy")
        _proxy_url = (
            sys.argv[_proxy_idx + 1]
            if _proxy_idx + 1 < len(sys.argv) and not sys.argv[_proxy_idx + 1].startswith("--")
            else "http://localhost:18080"
        )
        os.environ.setdefault("HTTP_PROXY",  _proxy_url)
        os.environ.setdefault("HTTPS_PROXY", _proxy_url)
        os.environ.setdefault("http_proxy",  _proxy_url)
        os.environ.setdefault("https_proxy", _proxy_url)

    _version = "unknown"
    if os.path.exists("version"):
        with open("version", "r") as f:
            _version = f.read().strip()
    _flags = []
    if '--autoupdate' in sys.argv: _flags.append('--autoupdate')
    if '--no-update' in sys.argv: _flags.append('--no-update')
    if '--no-venv' in sys.argv: _flags.append('--no-venv')
    if '--no-dm' in sys.argv: _flags.append('--no-dm')
    if '--repair' in sys.argv: _flags.append('--repair')
    if '--break-system-packages' in sys.argv: _flags.append('--break-system-packages')
    if _proxy_url: _flags.append('--proxy')
    startup.header(_version, f"{sys.version_info.major}.{sys.version_info.minor}", _flags or None)
    if _proxy_url:
        startup.info(f"프록시를 통해 게임 트래픽을 우회 전송합니다: {_proxy_url}")

    mutually_exclusive_flags = ["--autoupdate", "--no-update", "--repair"]
    active_flags = [flag for flag in mutually_exclusive_flags if flag in sys.argv]
    
    if len(active_flags) > 1:
        startup.error_box(
            "잘못된 인수",
            f"{' 와 '.join(active_flags)} 옵션은 동시에 사용할 수 없습니다.\n"
            "--autoupdate: 확인 절차 없이 자동으로 업데이트를 설치합니다.\n"
            "--no-update: 모든 업데이트 확인을 스킵합니다.\n"
            "--repair: 누락되거나 손상된 파일을 강제로 재설치 및 복구합니다."
        )
        sys.exit(1)

    def install_packages(requirements_txt_path: str, debug: bool = False) -> bool:
        full_command = [sys.executable, "-m", "pip", "install", "-r", requirements_txt_path, "--no-cache-dir"]

        if break_system_packages_arg():
            full_command.append("--break-system-packages")

        try:
            if debug:
                subprocess.check_call(full_command, timeout=1200, env=_pip_env())
                return True
            result = subprocess.run(full_command, timeout=1200, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True, env=_pip_env())
            if result.returncode == 0:
                return True
        except Exception:
            pass

        startup.warn("일부 패키지 동시 설치 실패 - 개별적으로 재설치 시도 중...")
        return _install_requirements_individually(requirements_txt_path)

    async def check_and_update_files():
        beta_mode = "--beta" in sys.argv
        repair_mode = "--repair" in sys.argv
        if repair_mode and not beta_mode and os.path.exists("version"):
            with open("version", "r") as f:
                if f.read().strip().startswith("beta-"):
                    beta_mode = True
                    print("  복구 모드: 베타 버전을 감지하여 main 브랜치에서 다시 복구합니다.")
        release_info = get_latest_release_info(beta_mode=beta_mode)
        
        if release_info:
            latest_tag = release_info["tag_name"]
            source_name = release_info["source"]
            
            if repair_mode:
                print(f"  복구 모드: {latest_tag} 버전으로 재설치를 강제 수행합니다.")
                current_version = "repair-mode"
            elif os.path.exists("version"):
                with open("version", "r") as f:
                    current_version = f.read().strip()
            else:
                current_version = "v0.0.0"

            is_upgrade = repair_mode or beta_mode or _is_newer_version(latest_tag, current_version)

            if (current_version != latest_tag and is_upgrade) or repair_mode:
                if repair_mode:
                    print(f"  설치 복구 진행 중: {latest_tag} (출처: {source_name})")
                    print("  기존 파일을 덮어쓰고 누락된 구성 요소를 다시 복구합니다.")
                else:
                    startup.update_available(latest_tag, source_name)
                    print(f"  업데이트 설명: {release_info['body']}")
                print()

                update = False

                if not is_container():
                    if "--autoupdate" in sys.argv or repair_mode:
                        update = True
                    elif not sys.stdin or not sys.stdin.isatty():
                        startup.phase_fail(
                            "업데이트 스킵됨",
                            details=["비대화형 터미널입니다 - 자동으로 업데이트를 진행하려면 --autoupdate 인자를 추가하세요."],
                        )
                        return
                    else:
                        print("  참고: 터미널 입력이 불가능한 경우 --autoupdate 인자를 주어 대기 없이 업데이트할 수 있습니다.")
                        ask = input("  업데이트를 진행하시겠습니까? (y/n): ").strip().lower()
                        update = ask == "y"
                else:
                    update = True
                    
                if update:
                    if os.path.exists("requirements.txt"):
                        try:
                            shutil.copy2("requirements.txt", "requirements.old")
                        except Exception:
                            pass

                    if os.path.exists("db") and os.path.isdir("db"):
                        db_bak_path = "db.bak"
                        if os.path.exists(db_bak_path) and os.path.isdir(db_bak_path):
                            if not safe_remove(db_bak_path):
                                db_bak_path = f"db.bak_{int(datetime.now().timestamp())}"

                        try:
                            shutil.copytree("db", db_bak_path)
                        except Exception:
                            pass

                    download_url = release_info["download_url"]
                    if not download_url:
                        startup.phase_fail("업데이트 실패", details=["다운로드 URL을 찾을 수 없습니다."])
                        return

                    startup.phase_start(f"{source_name} 에서 업데이트 파일 다운로드 중")
                    safe_remove("package.zip")
                    try:
                        download_resp = requests.get(download_url, timeout=600)
                    except Exception as e:
                        startup.phase_fail("업데이트 실패", details=[f"다운로드 중 오류 발생: {e}"])
                        return

                    if download_resp.status_code == 200:
                        with open("package.zip", "wb") as f:
                            f.write(download_resp.content)
                        
                        if os.path.exists("update") and os.path.isdir("update"):
                            if not safe_remove("update"):
                                startup.phase_fail("업데이트 실패", details=["이전 업데이트 임시 디렉터리를 삭제하지 못했습니다."])
                                return

                        try:
                            shutil.unpack_archive("package.zip", "update", "zip")
                        except Exception as e:
                            startup.phase_fail("업데이트 실패", details=[f"압축 해제 실패: {e}"])
                            return
                            
                        safe_remove("package.zip")
                        
                        update_dir = "update"
                        extracted_items = os.listdir(update_dir)
                        if len(extracted_items) == 1 and os.path.isdir(os.path.join(update_dir, extracted_items[0])):
                            update_dir = os.path.join(update_dir, extracted_items[0])
                        
                        main_py_path = os.path.join(update_dir, "main.py")
                        if os.path.exists(main_py_path):
                            safe_remove("main.py.bak")
                                
                            try:
                                if os.path.exists("main.py"):
                                    os.rename("main.py", "main.py.bak")
                            except Exception:
                                safe_remove("main.py")

                            try:
                                shutil.copy2(main_py_path, "main.py")
                            except Exception as e:
                                startup.phase_fail("업데이트 실패", details=[f"신규 main.py 설치 실패: {e}"])
                                return
                            
                        requirements_path = os.path.join(update_dir, "requirements.txt")
                        if os.path.exists(requirements_path):
                            success = install_packages(requirements_path, debug="--verbose" in sys.argv or "--debug" in sys.argv)

                            if success:
                                try:
                                    if os.path.exists("requirements.txt"):
                                        safe_remove("requirements.txt", is_dir=False)
                                    shutil.copy2(requirements_path, "requirements.txt")
                                except Exception:
                                    pass

                                cleanup_removed_packages()
                            else:
                                return
                            
                            safe_remove(requirements_path)
                            
                        for root, _, files in os.walk(update_dir):
                            for file in files:
                                if file == "main.py":
                                    continue
                                    
                                src_path = os.path.join(root, file)
                                rel_path = os.path.relpath(src_path, update_dir)
                                dst_path = os.path.join(".", rel_path)
                                norm_path = dst_path.replace("\\", "/")

                                if file in ["bot_token.txt", "version"] or norm_path.startswith("db/") or norm_path.startswith("./db/"):
                                    continue

                                os.makedirs(os.path.dirname(dst_path), exist_ok=True)

                                is_cogs_file = (norm_path.startswith("cogs/") or norm_path.startswith("./cogs/")) and file.endswith(".py")
                                
                                if is_cogs_file and os.path.exists(dst_path):
                                    src_hash = calculate_file_hash(src_path)
                                    dst_hash = calculate_file_hash(dst_path)
                                    
                                    if src_hash != dst_hash:
                                        cogs_bak_dir = "cogs.bak"
                                        os.makedirs(cogs_bak_dir, exist_ok=True)
                                        
                                        rel_path_in_cogs = os.path.relpath(dst_path, "cogs")
                                        backup_path = os.path.join(cogs_bak_dir, rel_path_in_cogs)
                                        
                                        os.makedirs(os.path.dirname(backup_path), exist_ok=True)
                                        
                                        try:
                                            if os.path.exists(backup_path):
                                                safe_remove(backup_path, is_dir=False)
                                            shutil.copy2(dst_path, backup_path)
                                        except Exception:
                                            pass

                                try:
                                    shutil.copy2(src_path, dst_path)
                                except Exception:
                                    pass
                        
                        safe_remove("update")

                        with open("version", "w") as f:
                            f.write(latest_tag)

                        startup.phase_ok(f"업데이트 완료 ({latest_tag} - 출처: {source_name})")

                        restart_process()
                    else:
                        startup.phase_fail("업데이트 실패", details=[f"HTTP 오류 코드 {download_resp.status_code} ({source_name})"])
                        return
            else:
                if current_version != latest_tag and not is_upgrade:
                    startup.phase_ok(f"구버전({latest_tag}) 업데이트 무시됨. 현재 버전을 유지합니다: {current_version}")
                else:
                    startup.up_to_date(current_version, source_name)
        else:
            startup.phase_fail("업데이트 확인 실패", details=["업데이트 서버 정보를 가져올 수 없습니다."])
        
    import asyncio
    from datetime import datetime
            
    if "--no-update" in sys.argv:
        startup.phase_ok("업데이트 검사 생략됨")
    else:
        asyncio.run(check_and_update_files())
            
    import discord
    from discord.ext import commands
    import sqlite3
    import logging
    import logging.handlers

    def setup_logging():
        """중앙 로그 수집 설정"""
        log_dir = 'log'
        os.makedirs(log_dir, exist_ok=True)

        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        simple_formatter = logging.Formatter('%(asctime)s - %(message)s')

        categories = {
            'alliance': 'alliance.txt',
            'gift': 'gift.txt',
            'redemption': 'redemption.txt',
            'notification': 'notification.txt',
            'bot': 'bot.txt',
        }

        handlers = {}
        for category, filename in categories.items():
            handler = logging.handlers.RotatingFileHandler(
                os.path.join(log_dir, filename),
                maxBytes=2 * 1024 * 1024,
                backupCount=1,
                encoding='utf-8'
            )
            if category == 'redemption':
                handler.setFormatter(simple_formatter)
            else:
                handler.setFormatter(formatter)
            handler.setLevel(logging.INFO)
            handlers[category] = handler

        for category, handler in handlers.items():
            logger = logging.getLogger(category)
            logger.setLevel(logging.INFO)
            logger.propagate = False
            if not logger.hasHandlers():
                logger.addHandler(handler)

        root_logger = logging.getLogger()
        root_logger.setLevel(logging.ERROR)

    setup_logging()

    os.environ.setdefault('TQDM_DISABLE', '1')

    rapidocr_log_path = os.path.join('log', 'rapidocr.txt')
    rapidocr_handler = logging.handlers.RotatingFileHandler(
        rapidocr_log_path, maxBytes=2 * 1024 * 1024, backupCount=1, encoding='utf-8',
    )
    rapidocr_handler.setFormatter(
        logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    )
    rapidocr_handler.setLevel(logging.INFO)
    for noisy_logger in ['RapidOCR', 'rapidocr', 'rapidocr.main', 'rapidocr.base',
                         'rapidocr.download_file', 'onnxruntime']:
        _noisy = logging.getLogger(noisy_logger)
        for h in list(_noisy.handlers):
            _noisy.removeHandler(h)
        _noisy.setLevel(logging.INFO)
        _noisy.propagate = False
        _noisy.addHandler(rapidocr_handler)

    class _ConsoleFilter:
        PATTERNS = ['[ERROR]', '[WARNING]', '[INFO]', '[SYNC]', '[ORPHAN CHECK]',
                    '[AUTO-DISABLE]', '[MONITOR]', '[RapidOCR]']

        def __init__(self, original):
            self._original = original
            self._logger = logging.getLogger('bot')

        def write(self, text):
            stripped = text.strip()
            if stripped and any(stripped.startswith(p) for p in self.PATTERNS):
                self._logger.info(stripped)
            else:
                self._original.write(text)

        def flush(self):
            self._original.flush()

        def __getattr__(self, name):
            return getattr(self._original, name)

    sys.stdout = _ConsoleFilter(sys.stdout)

    class CustomBot(commands.Bot):
        no_dm: bool = False

        async def on_error(self, event_name, *args, **kwargs):
            if event_name == "on_interaction":
                error = sys.exc_info()[1]
                if isinstance(error, discord.NotFound) and error.code == 10062:
                    return
            
            await super().on_error(event_name, *args, **kwargs)

        async def on_command_error(self, ctx: commands.Context, error: commands.CommandError) -> None:
            if isinstance(error, discord.NotFound) and error.code == 10062:
                return
            await super().on_command_error(ctx, error)

    intents = discord.Intents.default()
    intents.message_content = True

    bot = CustomBot(command_prefix="/", intents=intents)
    bot.no_dm = '--no-dm' in sys.argv

    token_file = "bot_token.txt"
    if not os.path.exists(token_file):
        bot_token = input("디스코드 봇 토큰을 입력하세요: ")
        with open(token_file, "w") as f:
            f.write(bot_token)
    else:
        with open(token_file, "r") as f:
            bot_token = f.read().strip()

    if not os.path.exists("db"):
        os.makedirs("db")

    databases = {
        "conn_alliance": "db/alliance.sqlite",
        "conn_giftcode": "db/giftcode.sqlite",
        "conn_changes": "db/changes.sqlite",
        "conn_users": "db/users.sqlite",
        "conn_settings": "db/settings.sqlite",
        "conn_id_channel": "db/id_channel.sqlite",
    }

    connections = {name: sqlite3.connect(path) for name, path in databases.items()}

    def create_tables():
        with connections["conn_changes"] as conn_changes:
            conn_changes.execute("""CREATE TABLE IF NOT EXISTS nickname_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                fid INTEGER, 
                old_nickname TEXT, 
                new_nickname TEXT, 
                change_date TEXT
            )""")
            
            conn_changes.execute("""CREATE TABLE IF NOT EXISTS furnace_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT, 
                fid INTEGER, 
                old_furnace_lv INTEGER, 
                new_furnace_lv INTEGER, 
                change_date TEXT
            )""")

        with connections["conn_settings"] as conn_settings:
            conn_settings.execute("""CREATE TABLE IF NOT EXISTS botsettings (
                id INTEGER PRIMARY KEY,
                channelid INTEGER,
                giftcodestatus TEXT
            )""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS bot_global_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT
            )""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS admin (
                id INTEGER PRIMARY KEY,
                is_initial INTEGER
            )""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS process_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'queued',
                priority INTEGER NOT NULL,
                alliance_id INTEGER,
                details TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                completed_at TEXT
            )""")
            conn_settings.execute("""CREATE INDEX IF NOT EXISTS idx_process_queue_status_priority
                ON process_queue(status, priority, id)""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS ocr_channel_settings (
                channel_id INTEGER PRIMARY KEY,
                alliance_id INTEGER NOT NULL,
                post_info_message INTEGER DEFAULT 1,
                pin_info_message INTEGER DEFAULT 1,
                info_message_id INTEGER,
                auto_delete_screenshots INTEGER DEFAULT 1
            )""")
            try:
                conn_settings.execute("SELECT auto_delete_screenshots FROM ocr_channel_settings LIMIT 1")
            except sqlite3.OperationalError:
                conn_settings.execute(
                    "ALTER TABLE ocr_channel_settings ADD COLUMN auto_delete_screenshots INTEGER DEFAULT 1"
                )
            conn_settings.execute("""CREATE TABLE IF NOT EXISTS ocr_channel_event_keywords (
                channel_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                keyword TEXT NOT NULL,
                PRIMARY KEY (channel_id, event_type, keyword)
            )""")
            conn_settings.execute("""CREATE INDEX IF NOT EXISTS idx_ocr_keywords_channel
                ON ocr_channel_event_keywords(channel_id)""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS ocr_channel_enabled_events (
                channel_id INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                PRIMARY KEY (channel_id, event_type)
            )""")
            conn_settings.execute("""CREATE INDEX IF NOT EXISTS idx_ocr_enabled_channel
                ON ocr_channel_enabled_events(channel_id)""")
            conn_settings.execute("""INSERT OR IGNORE INTO ocr_channel_enabled_events
                (channel_id, event_type)
                SELECT DISTINCT channel_id, event_type FROM ocr_channel_event_keywords""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS permission_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                actor_id INTEGER NOT NULL,
                action TEXT NOT NULL,
                target_id INTEGER NOT NULL,
                before_state TEXT,
                after_state TEXT,
                timestamp TEXT NOT NULL
            )""")
            conn_settings.execute("""CREATE INDEX IF NOT EXISTS idx_permission_audit_timestamp
                ON permission_audit_log(timestamp DESC)""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS admin_role (
                role_id     INTEGER PRIMARY KEY,
                guild_id    INTEGER,
                is_initial  INTEGER NOT NULL DEFAULT 0
            )""")
            conn_settings.execute("""CREATE TABLE IF NOT EXISTS adminserver_role (
                role_id      INTEGER NOT NULL,
                alliances_id INTEGER NOT NULL,
                PRIMARY KEY (role_id, alliances_id)
            )""")

            conn_settings.execute("""CREATE TABLE IF NOT EXISTS redemption_summary_settings (
                alliance_id INTEGER PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 0,
                show_success INTEGER NOT NULL DEFAULT 0,
                show_already INTEGER NOT NULL DEFAULT 0,
                show_failed INTEGER NOT NULL DEFAULT 1
            )""")

        with connections["conn_users"] as conn_users:
            conn_users.execute("""CREATE TABLE IF NOT EXISTS users (
                fid INTEGER PRIMARY KEY,
                nickname TEXT,
                furnace_lv INTEGER DEFAULT 0,
                kid INTEGER,
                stove_lv_content TEXT,
                alliance TEXT
            )""")
            _users_columns_to_add = [
                ("power",                   "INTEGER"),
                ("power_updated_at",        "TEXT"),
                ("combat_power",            "INTEGER"),
                ("combat_power_updated_at", "TEXT"),
                ("discord_id",              "INTEGER"),
                ("discord_server_id",       "INTEGER"),
                ("discord_id_updated_at",   "TEXT"),
                ("state_mismatch_at",       "TEXT"),
            ]
            for _col, _typ in _users_columns_to_add:
                try:
                    conn_users.execute(f"SELECT {_col} FROM users LIMIT 1")
                except sqlite3.OperationalError:
                    conn_users.execute(f"ALTER TABLE users ADD COLUMN {_col} {_typ}")
            conn_users.execute(
                "CREATE INDEX IF NOT EXISTS idx_users_discord_id ON users(discord_id)"
            )

        with connections["conn_giftcode"] as conn_giftcode:
            conn_giftcode.execute("""CREATE TABLE IF NOT EXISTS gift_codes (
                giftcode TEXT PRIMARY KEY, 
                date TEXT
            )""")
            
            conn_giftcode.execute("""CREATE TABLE IF NOT EXISTS user_giftcodes (
                fid INTEGER,
                giftcode TEXT,
                status TEXT,
                last_attempt_at TEXT,
                PRIMARY KEY (fid, giftcode),
                FOREIGN KEY (giftcode) REFERENCES gift_codes (giftcode)
            )""")
            uc_cols = [row[1] for row in conn_giftcode.execute("PRAGMA table_info(user_giftcodes)").fetchall()]
            if "last_attempt_at" not in uc_cols:
                conn_giftcode.execute("ALTER TABLE user_giftcodes ADD COLUMN last_attempt_at TEXT")

        with connections["conn_id_channel"] as conn_id_channel:
            conn_id_channel.execute("""CREATE TABLE IF NOT EXISTS id_channels (
                guild_id INTEGER,
                alliance_id INTEGER,
                channel_id INTEGER,
                created_at TEXT,
                created_by INTEGER,
                UNIQUE(guild_id, channel_id)
            )""")
            _idc_cols = {r[1] for r in conn_id_channel.execute("PRAGMA table_info(id_channels)")}
            if "info_message_id" not in _idc_cols:
                conn_id_channel.execute("ALTER TABLE id_channels ADD COLUMN info_message_id INTEGER")

        with connections["conn_alliance"] as conn_alliance:
            conn_alliance.execute("""CREATE TABLE IF NOT EXISTS alliancesettings (
                alliance_id INTEGER PRIMARY KEY,
                channel_id INTEGER,
                interval INTEGER
            )""")

            try:
                conn_alliance.execute("SELECT auto_remove_on_transfer FROM alliancesettings LIMIT 1")
            except sqlite3.OperationalError:
                conn_alliance.execute(
                    "ALTER TABLE alliancesettings ADD COLUMN auto_remove_on_transfer INTEGER DEFAULT 0"
                )

            for _col, _default in (("id_post_info_message", "0"), ("id_pin_info_message", "1")):
                try:
                    conn_alliance.execute(f"SELECT {_col} FROM alliancesettings LIMIT 1")
                except sqlite3.OperationalError:
                    conn_alliance.execute(
                        f"ALTER TABLE alliancesettings ADD COLUMN {_col} INTEGER DEFAULT {_default}"
                    )

            try:
                conn_alliance.execute("SELECT ocr_upload_admin_only FROM alliancesettings LIMIT 1")
            except sqlite3.OperationalError:
                conn_alliance.execute(
                    "ALTER TABLE alliancesettings ADD COLUMN ocr_upload_admin_only INTEGER DEFAULT 0"
                )

            try:
                conn_alliance.execute("SELECT silent_notifications FROM alliancesettings LIMIT 1")
            except sqlite3.OperationalError:
                conn_alliance.execute(
                    "ALTER TABLE alliancesettings ADD COLUMN silent_notifications INTEGER DEFAULT 0"
                )

            try:
                conn_alliance.execute("SELECT redemption_channel_id FROM alliancesettings LIMIT 1")
            except sqlite3.OperationalError:
                conn_alliance.execute(
                    "ALTER TABLE alliancesettings ADD COLUMN redemption_channel_id INTEGER"
                )
                conn_alliance.execute(
                    "UPDATE alliancesettings SET redemption_channel_id = channel_id"
                )

            with connections["conn_settings"] as _cs:
                _cs.execute("""CREATE TABLE IF NOT EXISTS bot_global_settings (
                    setting_key TEXT PRIMARY KEY, setting_value TEXT)""")
                _done = _cs.execute(
                    "SELECT 1 FROM bot_global_settings WHERE setting_key = 'sync_log_migrated'"
                ).fetchone()
            if not _done:
                conn_alliance.execute(
                    "UPDATE alliancesettings SET redemption_channel_id = channel_id "
                    "WHERE redemption_channel_id IS NULL AND channel_id IS NOT NULL"
                )
                with connections["conn_settings"] as _cs:
                    _cs.execute(
                        "INSERT OR REPLACE INTO bot_global_settings VALUES ('sync_log_migrated', '1')"
                    )

            with connections["conn_settings"] as _cs:
                _giftfb_done = _cs.execute(
                    "SELECT 1 FROM bot_global_settings WHERE setting_key = 'redeem_channel_gift_fallback'"
                ).fetchone()
            if not _giftfb_done:
                try:
                    _gift_channels = connections["conn_giftcode"].execute(
                        "SELECT alliance_id, channel_id FROM giftcode_channel WHERE channel_id IS NOT NULL"
                    ).fetchall()
                except sqlite3.OperationalError:
                    _gift_channels = []
                for _aid, _cid in _gift_channels:
                    conn_alliance.execute(
                        "UPDATE alliancesettings SET redemption_channel_id = ? "
                        "WHERE alliance_id = ? AND redemption_channel_id IS NULL",
                        (_cid, _aid),
                    )
                with connections["conn_settings"] as _cs:
                    _cs.execute(
                        "INSERT OR REPLACE INTO bot_global_settings VALUES ('redeem_channel_gift_fallback', '1')"
                    )

            conn_alliance.execute("""CREATE TABLE IF NOT EXISTS alliance_list (
                alliance_id INTEGER PRIMARY KEY,
                name TEXT,
                discord_server_id INTEGER,
                kid INTEGER
            )""")
            existing_cols = [row[1] for row in conn_alliance.execute("PRAGMA table_info(alliance_list)").fetchall()]
            if "discord_server_id" not in existing_cols:
                conn_alliance.execute("ALTER TABLE alliance_list ADD COLUMN discord_server_id INTEGER")
            if "kid" not in existing_cols:
                conn_alliance.execute("ALTER TABLE alliance_list ADD COLUMN kid INTEGER")
            if "multistate" not in existing_cols:
                conn_alliance.execute("ALTER TABLE alliance_list ADD COLUMN multistate INTEGER DEFAULT 0")
            if "state_locked" not in existing_cols:
                conn_alliance.execute("ALTER TABLE alliance_list ADD COLUMN state_locked INTEGER DEFAULT 0")
                conn_alliance.execute("UPDATE alliance_list SET state_locked = 1 WHERE kid IS NOT NULL")

    raise_open_file_limit()
    create_tables()
    startup.phase_ok("데이터베이스 연결 준비 완료")

    async def load_cogs():
        cogs = ["pimp_my_bot", "process_queue", "onnx_lifecycle", "bot_main_menu", "alliance", "alliance_member_operations", "bot_operations", "alliance_logs", "bot_support", "bot_health", "gift_operations", "alliance_history", "alliance_w_command", "bot_startup", "notification_system", "notification_schedule", "alliance_id_channel", "alliance_channels", "bot_backup", "notification_editor", "notification_templates", "notification_wizard", "attendance", "attendance_report", "attendance_ocr", "minister_schedule", "minister_menu", "minister_archive", "alliance_registration", "bear_track"]

        failed_cogs = []

        logging.disable(logging.INFO)
        with open(os.devnull, 'w') as devnull, \
                contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            for cog in cogs:
                try:
                    await bot.load_extension(f"cogs.{cog}")
                except Exception as e:
                    failed_cogs.append((cog, str(e)))
        logging.disable(logging.NOTSET)

        total = len(cogs)
        loaded = total - len(failed_cogs)
        if failed_cogs:
            startup.phase_fail(
                f"기능 모듈 로드 완료 ({loaded}/{total})",
                details=[f"{cog}: {error}" for cog, error in failed_cogs],
                fix="python main.py --repair 명령어로 복구를 진행해보세요."
            )
        else:
            startup.phase_ok(f"모든 기능 모듈 로드 완료 ({loaded}/{total})")

    @bot.event
    async def on_ready():
        try:
            startup.phase_ok(f"디스코드 연결 성공: {bot.user}")
            if not getattr(bot, '_commands_synced', False):
                startup.phase_start("디스코드 슬래시(/) 명령어 동기화 중")
                await bot.tree.sync()
                bot._commands_synced = True
                startup.phase_ok("디스코드 슬래시(/) 명령어 동기화 완료")

            try:
                import aiohttp as _aio
                timeout = _aio.ClientTimeout(total=5)
                async with _aio.ClientSession(timeout=timeout, trust_env=True) as session:
                    startup.phase_start("기프트 코드 분배 API 서버 확인 중")
                    headers = {'X-API-Key': 'super_secret_bot_token_nobody_will_ever_find'}
                    url = "http://gift-code-api.whiteout-bot.com/giftcode_api.php"

                    async def _probe_distribution():
                        try:
                            async with session.get(url, headers=headers) as resp:
                                if resp.status < 500:
                                    return "ok", None
                                return "error", f"HTTP 오류 {resp.status}"
                        except TimeoutError:
                            return "error", "응답 시간 초과"
                        except _aio.ClientError:
                            return "error", "오프라인 (연결 실패)"

                    state, detail = await _probe_distribution()
                    if state != "ok":
                        state, detail = await _probe_distribution()
                    startup.api_status("기프트 코드 분배 API", state, detail)

                try:
                    startup.phase_start("기프트 코드 입력 API 서버 확인 중")
                    health = bot.get_cog("BotHealth")
                    if health is not None:
                        result = await health.check_wos_api_status()
                        ok = result.get("status") in ("healthy", "warning")
                        detail = result.get("message")
                        startup.api_status("기프트 코드 입력 API", "ok" if ok else "error",
                                           None if detail in ("Online", "온라인") else detail)
                    else:
                        startup.api_status("기프트 코드 입력 API", "error", "Health 모듈 미로드")
                except Exception:
                    startup.api_status("기프트 코드 입력 API", "error", "검사 실패")

                try:
                    from cogs.bear_track import remote_ocr_url, OCR_REMOTE_TOKEN
                    ocr_url = remote_ocr_url()
                    if not ocr_url:
                        startup.phase_ok("로컬 OCR 인식을 사용합니다.")
                    else:
                        startup.phase_start("외부 OCR 서비스 상태 확인 중")
                        try:
                            async with _aio.ClientSession(timeout=timeout, trust_env=True) as ocr_session:
                                async with ocr_session.get(
                                    f"{ocr_url}/health",
                                    headers={"X-API-Key": OCR_REMOTE_TOKEN},
                                ) as resp:
                                    ok = resp.status == 200
                                    ocr_detail = None if ok else f"HTTP 오류 {resp.status}"
                            startup.api_status("외부 OCR 서비스", "ok" if ok else "error", ocr_detail)
                        except Exception:
                            startup.api_status("외부 OCR 서비스", "error", "연결 불가 (로컬 OCR로 전환)")
                except Exception:
                    pass
            except Exception:
                pass

            alliance_details = []
            try:
                import sqlite3 as _sq
                with _sq.connect('db/alliance.sqlite') as _c:
                    alliances = _c.execute("SELECT alliance_id, name FROM alliance_list ORDER BY name").fetchall()
                    alliance_count = len(alliances)
                with _sq.connect('db/users.sqlite') as _c:
                    member_count = _c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
                    for aid, name in alliances:
                        count = _c.execute("SELECT COUNT(*) FROM users WHERE alliance = ?", (aid,)).fetchone()[0]
                        alliance_details.append((name, count))
            except Exception:
                alliance_count = 0
                member_count = 0

            startup.summary(len(bot.guilds), alliance_count, member_count, alliance_details or None)

            await asyncio.sleep(2)
            if getattr(bot, 'no_dm', False):
                startup.info("시작 DM 전송이 스킵되었습니다.")
            elif getattr(bot, 'startup_dm_sent', False):
                startup.info("전역 관리자에게 시작 DM 전송 완료")

            startup.ready()
        except Exception as e:
            logging.getLogger('bot').error(f"명령어 동기화 중 오류 발생: {e}")
            print(f"명령어 동기화 중 오류 발생: {e}")

    async def main():
        await load_cogs()

        startup.phase_start("디스코드 서버에 연결하는 중")
        attempt = 0
        while True:
            attempt += 1
            try:
                await bot.start(bot_token)
                break

            except discord.LoginFailure:
                startup.error_box("로그인 실패", "봇 토큰이 올바르지 않습니다.", "bot_token.txt 파일을 확인하세요.")
                break

            except discord.PrivilegedIntentsRequired:
                startup.error_box("로그인 실패", "특권 인텐트(Privileged Intents) 설정이 활성화되지 않았습니다.", "디스코드 개발자 센터에서 Message Content Intent 옵션을 켜주세요.")
                break

            except discord.HTTPException as e:
                if e.status == 429:
                    startup.connection_retry(attempt, "요청 제한(Rate Limit)", 60)
                    await asyncio.sleep(60)
                elif e.status >= 500:
                    startup.connection_retry(attempt, f"서버 오류 (HTTP {e.status})", 30)
                    await asyncio.sleep(30)
                else:
                    startup.connection_retry(attempt, f"HTTP {e.status}", 30)
                    await asyncio.sleep(30)

            except discord.GatewayNotFound:
                startup.connection_retry(attempt, "게이트웨이를 찾을 수 없음", 30)
                await asyncio.sleep(30)

            except (aiohttp.ClientConnectorDNSError, aiohttp.ClientConnectorError):
                startup.connection_retry(attempt, "네트워크 연결 실패", 30)
                await asyncio.sleep(30)

            except OSError as e:
                if e.errno in (-3, 11001):
                    startup.connection_retry(attempt, "DNS 조회 실패", 30)
                    await asyncio.sleep(30)
                else:
                    raise

    def run_bot():
        import signal

        async def start_bot():
            stop_event = asyncio.Event()

            def signal_handler():
                if is_container():
                    print(f"\n  종료 신호를 수신했습니다. 안전하게 종료합니다...")
                else:
                    print(f"\n  {startup.shutdown()}")
                stop_event.set()

            loop = asyncio.get_running_loop()

            if sys.platform != "win32":
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(sig, signal_handler)
            else:
                def win_handler(signum, frame):
                    signal_handler()
                signal.signal(signal.SIGINT, win_handler)

            bot_task = asyncio.create_task(main())

            shutdown_task = asyncio.create_task(stop_event.wait())
            done, pending = await asyncio.wait(
                [bot_task, shutdown_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            try:
                from discord.ext import tasks as _tasks_mod
                loop_tasks = []
                for cog in list(bot.cogs.values()):
                    for attr in dir(cog):
                        try:
                            obj = getattr(cog, attr)
                        except Exception:
                            continue
                        if isinstance(obj, _tasks_mod.Loop):
                            obj.cancel()
                            t = obj.get_task()
                            if t is not None:
                                loop_tasks.append(t)
                pq = bot.get_cog("ProcessQueue")
                if pq is not None:
                    pq._shutting_down = True
                    pq._wake_event.set()
                    pqt = getattr(pq, "_processor_task", None)
                    if pqt is not None and not pqt.done():
                        pqt.cancel()
                        loop_tasks.append(pqt)
                if loop_tasks:
                    await asyncio.wait(loop_tasks, timeout=3.0)
            except Exception as e:
                print(f"  백그라운드 작업 종료 중 오류: {e}")

            if not bot.is_closed():
                await bot.close()

        try:
            asyncio.run(start_bot())
        except KeyboardInterrupt:
            pass

    run_bot()