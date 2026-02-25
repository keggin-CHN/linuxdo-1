"""
MissAV 视频下载器
基于协议分析，无需浏览器自动化
依赖: curl_cffi, ffmpeg (系统命令)
"""

import re
import subprocess
import sys
import os
from urllib.parse import urljoin, urlparse

try:
    from curl_cffi import requests
except ImportError:
    print("需要安装 curl_cffi: pip install curl_cffi")
    sys.exit(1)

USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36'

# ==================== 用户配置区域（可直接修改） ====================
CONFIG_TARGET_URL = 'https://missavtv.com/ja/sdde-698'
CONFIG_CLIP_START = '01:09:19'
CONFIG_CLIP_END = '01:09:48'
CONFIG_QUALITY = 'best'  # best(最高分辨率) / 1080 / 720 / 480 ...
# ==================================================================

DEFAULT_CLIP_START = CONFIG_CLIP_START
DEFAULT_CLIP_END = CONFIG_CLIP_END
DEFAULT_QUALITY = CONFIG_QUALITY
DEFAULT_PROXY = 'http://127.0.0.1:10808'


def find_ffmpeg() -> str:
    """查找 ffmpeg 可执行文件路径"""
    import shutil
    # 先检查 PATH
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path:
        return ffmpeg_path

    # winget 安装位置
    local_app = os.environ.get('LOCALAPPDATA', '')
    if local_app:
        import glob
        pattern = os.path.join(local_app, 'Microsoft', 'WinGet', 'Packages', '*FFmpeg*', '**', 'ffmpeg.exe')
        matches = glob.glob(pattern, recursive=True)
        if matches:
            return matches[0]

    # 常见位置
    common_paths = [
        r'C:\ffmpeg\bin\ffmpeg.exe',
        r'C:\Program Files\ffmpeg\bin\ffmpeg.exe',
    ]
    for p in common_paths:
        if os.path.exists(p):
            return p

    raise FileNotFoundError("未找到 ffmpeg，请安装: winget install Gyan.FFmpeg")


def extract_hls_url(html: str) -> str:
    """从页面 HTML 中提取 hlsSource URL (Next.js RSC 数据格式)"""
    # 方法1: 直接匹配 hlsSource 属性
    match = re.search(r'"hlsSource"\s*:\s*"([^"]+\.m3u8[^"]*)"', html)
    if match:
        url = match.group(1).replace('\\u0026', '&')
        return url

    # 方法2: 匹配所有 m3u8 URL
    m3u8_urls = re.findall(r'https?://[^\s"\'\\]+\.m3u8[^\s"\'\\]*', html)
    if m3u8_urls:
        return m3u8_urls[0]

    raise ValueError("未找到 m3u8 视频 URL")


def extract_title(html: str) -> str:
    """提取视频标题"""
    match = re.search(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL)
    if match:
        return match.group(1).strip()
    match = re.search(r'property="og:title"\s+content="([^"]+)"', html)
    if match:
        return match.group(1).strip()
    return 'unknown'


def extract_canonical_url(html: str) -> str | None:
    """提取页面 canonical/og:url 作为更准确的 Referer"""
    match = re.search(r'<link[^>]+rel=["\']canonical["\'][^>]+href=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    match = re.search(r'property=["\']og:url["\']\s+content=["\']([^"\']+)["\']', html, re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return None


def cookies_to_header(cookies: dict) -> str:
    """把 cookie dict 转为 HTTP Cookie 头"""
    if not cookies:
        return ''
    return '; '.join(f'{k}={v}' for k, v in cookies.items())


def parse_time_to_seconds(time_str: str) -> int:
    """解析时间字符串为秒，支持 HH:MM:SS / MM:SS / 纯秒数"""
    value = (time_str or '').strip()
    if not value:
        raise ValueError("时间参数不能为空")

    if re.fullmatch(r'\d+', value):
        return int(value)

    parts = value.split(':')
    if len(parts) == 2:
        mm, ss = parts
        if not (mm.isdigit() and ss.isdigit()):
            raise ValueError(f"无效时间格式: {time_str}")
        m, s = int(mm), int(ss)
        if s >= 60:
            raise ValueError(f"秒数必须 < 60: {time_str}")
        return m * 60 + s

    if len(parts) == 3:
        hh, mm, ss = parts
        if not (hh.isdigit() and mm.isdigit() and ss.isdigit()):
            raise ValueError(f"无效时间格式: {time_str}")
        h, m, s = int(hh), int(mm), int(ss)
        if m >= 60 or s >= 60:
            raise ValueError(f"分钟和秒数必须 < 60: {time_str}")
        return h * 3600 + m * 60 + s

    raise ValueError(f"无效时间格式: {time_str}")


def seconds_to_hhmmss(total_seconds: int) -> str:
    """把秒数转换为 HH:MM:SS"""
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"


def normalize_proxy(proxy: str | None) -> str | None:
    """标准化代理地址，支持 127.0.0.1:10808 / http:// / socks5://"""
    if not proxy:
        return None
    value = proxy.strip()
    if not value:
        return None
    if '://' not in value:
        value = f'http://{value}'
    return value


NON_MEDIA_EXTENSIONS = {
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.css', '.js',
    '.jpg', '.jpeg', '.png', '.gif', '.svg', '.webp',
    '.html', '.htm', '.json', '.xml', '.txt'
}


def is_suspicious_segment_url(url: str) -> bool:
    """判断分片 URL 是否明显是非媒体资源（用于过滤反爬伪分片）"""
    parsed = urlparse(url)
    path = (parsed.path or '').lower()
    if not path or path.endswith('/'):
        return False
    _, ext = os.path.splitext(path)
    if not ext:
        return False
    return ext in NON_MEDIA_EXTENSIONS


def fetch_local_media_playlist(
    m3u8_url: str,
    proxy: str = None,
    origin: str = None,
    referer: str = None,
    cookie_header: str = None
) -> str:
    """用 curl_cffi 拉取并重写媒体 m3u8 到本地文件，返回本地路径"""

    proxy_value = normalize_proxy(proxy)
    kwargs = {}
    if proxy_value:
        kwargs['proxies'] = {'https': proxy_value, 'http': proxy_value}

    session = requests.Session(impersonate="chrome136", **kwargs)
    origin_value = origin or 'https://missav.com'
    referer_value = referer or 'https://missav.com/'
    session.headers.update({
        'Origin': origin_value,
        'Referer': referer_value,
        'User-Agent': USER_AGENT
    })
    if cookie_header:
        session.headers.update({'Cookie': cookie_header})

    r = session.get(m3u8_url, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"拉取 m3u8 失败: {r.status_code}")

    playlist_url = str(getattr(r, 'url', m3u8_url))
    content = r.text.lstrip('\ufeff').strip()

    # 如果是 master playlist，再选一次最高分辨率
    if '#EXT-X-STREAM-INF' in content:
        best_url, _ = get_best_quality_url(playlist_url, session)
        r2 = session.get(best_url, timeout=30)
        if r2.status_code != 200:
            raise RuntimeError(f"拉取媒体 playlist 失败: {r2.status_code}")
        playlist_url = str(getattr(r2, 'url', best_url))
        content = r2.text.lstrip('\ufeff').strip()

    if '#EXTM3U' not in content:
        preview = content[:240].replace('\n', ' ')
        raise RuntimeError(f"返回内容不是 m3u8，预览: {preview}")

    lines = []
    total_segments = 0

    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line:
            lines.append('')
            continue

        if line.startswith('#'):
            if 'URI="' in line:
                line = re.sub(
                    r'URI="([^"]+)"',
                    lambda m: f'URI="{urljoin(playlist_url, m.group(1))}"',
                    line
                )
            lines.append(line)
            continue

        total_segments += 1
        lines.append(urljoin(playlist_url, line))

    if total_segments == 0:
        raise RuntimeError("媒体 playlist 未找到任何分片 URL")

    import tempfile
    fd, local_path = tempfile.mkstemp(prefix='missav_', suffix='.m3u8')
    os.close(fd)
    with open(local_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(lines) + '\n')
    return local_path


def clip_local_playlist_by_time(local_playlist_path: str, start_seconds: int | None, end_seconds: int | None) -> None:
    """按时间范围裁剪本地媒体 playlist（基于 #EXTINF 累积时长）"""
    if start_seconds is None and end_seconds is None:
        return

    clip_start = float(start_seconds or 0)
    clip_end = float(end_seconds) if end_seconds is not None else float('inf')
    if clip_end != float('inf') and clip_end <= clip_start:
        raise ValueError("结束时间必须大于开始时间")

    with open(local_playlist_path, 'r', encoding='utf-8') as f:
        original_lines = [line.rstrip('\r\n') for line in f]

    new_lines = []
    pending_segment_lines = []
    in_segment_block = False
    segment_duration = 0.0
    timeline = 0.0
    total_segments = 0
    kept_segments = 0

    for raw_line in original_lines:
        line = raw_line.strip()
        if not line:
            continue

        if line.startswith('#EXTINF'):
            in_segment_block = True
            pending_segment_lines = [line]
            m = re.search(r'#EXTINF:([0-9.]+)', line)
            segment_duration = float(m.group(1)) if m else 0.0
            continue

        if in_segment_block and line.startswith('#'):
            pending_segment_lines.append(line)
            continue

        if line.startswith('#'):
            if line == '#EXT-X-ENDLIST':
                continue
            new_lines.append(line)
            continue

        # segment URI
        total_segments += 1
        seg_start = timeline
        seg_end = timeline + segment_duration if in_segment_block else timeline
        keep = (seg_end > clip_start) and (seg_start < clip_end)

        if keep:
            if pending_segment_lines:
                new_lines.extend(pending_segment_lines)
            new_lines.append(line)
            kept_segments += 1

        if in_segment_block:
            timeline = seg_end
        pending_segment_lines = []
        in_segment_block = False
        segment_duration = 0.0

    if kept_segments == 0:
        duration_disp = seconds_to_hhmmss(int(timeline))
        raise RuntimeError(
            f"按时间裁剪后没有可用分片（目标起点 {seconds_to_hhmmss(int(clip_start))}, playlist 总时长约 {duration_disp}）"
        )

    if not any(x.strip() == '#EXT-X-ENDLIST' for x in new_lines):
        new_lines.append('#EXT-X-ENDLIST')

    with open(local_playlist_path, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(new_lines) + '\n')

    end_disp = seconds_to_hhmmss(int(clip_end)) if clip_end != float('inf') else 'END'
    print(f"已按时间裁剪本地 playlist: {seconds_to_hhmmss(int(clip_start))} - {end_disp}，保留分片 {kept_segments}/{total_segments}")


def get_best_quality_url(playlist_url: str, session, quality: str = 'best') -> tuple:
    """从 master playlist 选择清晰度，返回 (url, resolution)。
    quality=best 时取最高分辨率；否则可传 1080/720 等目标高度。
    """
    r = session.get(playlist_url)
    if r.status_code != 200:
        raise ValueError(f"请求 playlist 失败: {r.status_code}")

    content = r.text
    lines = content.strip().split('\n')

    # 非 master playlist，直接返回
    if '#EXT-X-STREAM-INF' not in content:
        return playlist_url, 0

    quality_value = str(quality or 'best').strip().lower()
    prefer_height = None
    if quality_value not in ('best', 'max', 'highest', 'source'):
        m = re.search(r'\d+', quality_value)
        if not m:
            raise ValueError(f"无效 quality 参数: {quality}（可用: best/1080/720/...）")
        prefer_height = int(m.group(0))

    candidates = []  # [(height, stream_url)]
    for i, line in enumerate(lines):
        if '#EXT-X-STREAM-INF' not in line:
            continue
        if i + 1 >= len(lines) or lines[i + 1].startswith('#'):
            continue

        height = 0
        res_match = re.search(r'RESOLUTION=\d+x(\d+)', line)
        if res_match:
            height = int(res_match.group(1))
        stream_url = lines[i + 1].strip()
        candidates.append((height, stream_url))

    if not candidates:
        for line in lines:
            if not line.startswith('#') and line.strip():
                candidates.append((0, line.strip()))
                break

    if not candidates:
        raise ValueError("未找到可用的视频流")

    if prefer_height is None:
        best_height, best_url = max(candidates, key=lambda x: x[0])
    else:
        with_height = [x for x in candidates if x[0] > 0]
        if not with_height:
            best_height, best_url = candidates[0]
        else:
            lower_or_equal = [x for x in with_height if x[0] <= prefer_height]
            if lower_or_equal:
                best_height, best_url = max(lower_or_equal, key=lambda x: x[0])
            else:
                best_height, best_url = min(with_height, key=lambda x: x[0])

    # 相对/绝对路径转完整 URL
    if not best_url.startswith('http'):
        parsed = urlparse(playlist_url)
        if best_url.startswith('/'):
            best_url = f"{parsed.scheme}://{parsed.netloc}{best_url}"
        else:
            base_url = playlist_url[:playlist_url.rfind('/') + 1]
            best_url = base_url + best_url

    return best_url, best_height


def run_ffmpeg(cmd: list[str], env: dict) -> tuple[int, str]:
    """运行 ffmpeg 并实时打印输出，返回 (exit_code, 全量输出)"""
    process = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        universal_newlines=True,
        encoding='utf-8',
        errors='replace',
        env=env
    )

    logs = []
    for line in process.stdout:
        logs.append(line)
        if 'time=' in line or 'size=' in line:
            print(f'\r{line.strip()}', end='', flush=True)
        elif 'error' in line.lower():
            print(line.strip())

    process.wait()
    return process.returncode, ''.join(logs)


def download_with_ffmpeg(
    m3u8_url: str,
    output_path: str,
    start_seconds: int = None,
    end_seconds: int = None,
    proxy: str = None,
    origin: str = None,
    referer: str = None,
    cookie_header: str = None
):
    """用 ffmpeg 下载 m3u8 流，可选裁剪时间段"""
    ffmpeg_bin = find_ffmpeg()

    origin_value = origin or 'https://missav.com'
    referer_value = referer or 'https://missav.com/'
    header_lines = [
        f'Origin: {origin_value}',
        f'Referer: {referer_value}',
        f'User-Agent: {USER_AGENT}',
    ]
    if cookie_header:
        header_lines.append(f'Cookie: {cookie_header}')
    ffmpeg_headers = '\r\n'.join(header_lines) + '\r\n'

    print(f"\n开始下载到: {output_path}")
    print(f"m3u8 URL: {m3u8_url}")
    if start_seconds is not None or end_seconds is not None:
        start_disp = seconds_to_hhmmss(start_seconds or 0)
        end_disp = seconds_to_hhmmss(end_seconds) if end_seconds is not None else 'END'
        print(f"时间段: {start_disp} - {end_disp}")
    print()

    env = os.environ.copy()
    proxy_value = normalize_proxy(proxy)
    if proxy_value:
        env['http_proxy'] = proxy_value
        env['https_proxy'] = proxy_value
        env['HTTP_PROXY'] = proxy_value
        env['HTTPS_PROXY'] = proxy_value
        print(f"使用代理下载分片: {proxy_value}")

    input_source = m3u8_url
    local_playlist = None

    # 优先本地化 playlist，避免 ffmpeg 直接请求远程 m3u8 被拦截
    try:
        local_playlist = fetch_local_media_playlist(
            m3u8_url,
            proxy=proxy,
            origin=origin_value,
            referer=referer_value,
            cookie_header=cookie_header
        )
        input_source = local_playlist
        print(f"已生成本地 playlist: {local_playlist}")
    except Exception as e:
        print(f"本地化 playlist 失败，回退远程直连: {e}")

    # 对本地 playlist 做时间裁剪，避免 ffmpeg 在 HLS 输入上 seek 到开头
    if local_playlist and (start_seconds is not None or end_seconds is not None):
        clip_local_playlist_by_time(local_playlist, start_seconds, end_seconds)
        # 已在 playlist 层裁剪，ffmpeg 阶段不再重复 -ss/-t
        start_seconds = None
        end_seconds = None

    def build_ffmpeg_cmd(include_headers: bool = True) -> list[str]:
        cmd = [ffmpeg_bin]

        if include_headers:
            cmd.extend(['-headers', ffmpeg_headers])

        if local_playlist:
            cmd.extend([
                '-allowed_extensions', 'ALL',
                '-extension_picky', '0',
                '-protocol_whitelist', 'file,http,https,tcp,tls,crypto,httpproxy'
            ])

        cmd.extend(['-i', input_source])

        if start_seconds is not None:
            cmd.extend(['-ss', seconds_to_hhmmss(start_seconds)])

        if end_seconds is not None:
            if start_seconds is None:
                cmd.extend(['-to', seconds_to_hhmmss(end_seconds)])
            else:
                duration = end_seconds - start_seconds
                if duration <= 0:
                    raise ValueError("结束时间必须大于开始时间")
                cmd.extend(['-t', str(duration)])

        cmd.extend([
            '-c', 'copy',
            '-y',
            output_path
        ])
        return cmd

    try:
        cmd = build_ffmpeg_cmd(include_headers=True)
        code, out = run_ffmpeg(cmd, env)

        if code != 0 and local_playlist and out and 'Option headers not found' in out:
            print("\n检测到当前 ffmpeg 在本地 playlist 模式下不接受 -headers，重试（不带 headers）...")
            cmd = build_ffmpeg_cmd(include_headers=False)
            code, out = run_ffmpeg(cmd, env)
    finally:
        if local_playlist and os.path.exists(local_playlist):
            try:
                os.remove(local_playlist)
            except OSError:
                pass

    if code == 0:
        if os.path.exists(output_path):
            size = os.path.getsize(output_path)
            print(f"\n\n下载完成! 文件大小: {size / 1024 / 1024:.1f} MB")
        else:
            raise RuntimeError("ffmpeg 完成但文件不存在")
    else:
        print(f"\n\nffmpeg 退出码: {code}")
        if out:
            preview = out[-500:].replace('\r', ' ').replace('\n', ' ')
            print(f"ffmpeg 最后输出: {preview}")
        raise RuntimeError("ffmpeg 下载失败")


def fetch_page(url: str, max_retries: int = 3, proxy: str = None) -> tuple[str, dict, str]:
    """尝试多种 impersonate 配置请求页面，支持重试和代理，返回 (html, cookies, final_url)"""
    import time
    impersonates = ["chrome136", "chrome124", "chrome120", "chrome116", "chrome110"]
    last_error = None

    for attempt in range(max_retries):
        if attempt > 0:
            wait = 2 ** attempt
            print(f"  等待 {wait} 秒后重试...")
            time.sleep(wait)

        for imp in impersonates:
            try:
                kwargs = {}
                if proxy:
                    kwargs['proxies'] = {'https': proxy, 'http': proxy}
                session = requests.Session(impersonate=imp, **kwargs)
                r = session.get(url, timeout=30)
                if r.status_code == 200:
                    print(f"  成功 (impersonate={imp}, 尝试 {attempt+1}), 内容长度: {len(r.text)}")
                    return r.text, dict(session.cookies), str(getattr(r, 'url', url))
                else:
                    print(f"  impersonate={imp} 状态码: {r.status_code}")
            except Exception as e:
                last_error = e
                if attempt == 0:
                    print(f"  impersonate={imp} 失败: {type(e).__name__}")
                continue

    raise RuntimeError(f"所有尝试均失败，最后错误: {last_error}")


def download_missav(
    url: str,
    output_dir: str = '.',
    proxy: str = DEFAULT_PROXY,
    html_file: str = None,
    info_only: bool = False,
    clip_start: str = None,
    clip_end: str = None,
    quality: str = DEFAULT_QUALITY
):
    """主下载流程"""

    proxy = normalize_proxy(proxy)
    if proxy:
        print(f"代理: {proxy}", file=sys.stderr)

    # 第一步: 请求页面
    if html_file and os.path.exists(html_file):
        print(f"[1/3] 从本地文件加载: {html_file}", file=sys.stderr)
        with open(html_file, 'r', encoding='utf-8') as f:
            html = f.read()
        page_cookies = {}
        final_url = url
    else:
        print(f"[1/3] 请求页面: {url}", file=sys.stderr)
        html, page_cookies, final_url = fetch_page(url, proxy=proxy)

    canonical_url = extract_canonical_url(html)
    effective_page_url = canonical_url or final_url or url

    parsed_page = urlparse(effective_page_url)
    page_origin = f"{parsed_page.scheme}://{parsed_page.netloc}" if parsed_page.scheme and parsed_page.netloc else 'https://missav.com'
    page_referer = effective_page_url
    cookie_header = cookies_to_header(page_cookies)

    # 第二步: 提取标题
    print("[2/3] 提取视频信息...", file=sys.stderr)
    print(f"  页面来源: {page_referer}", file=sys.stderr)
    title = extract_title(html)

    # 从 URL 提取番号
    code = url.rstrip('/').split('/')[-1].upper()
    display_title = f"{code} {title}" if code not in title.upper() else title

    # 清理文件名
    safe_title = re.sub(r'[\\/:*?"<>|]', '_', display_title)
    if len(safe_title) > 190:
        safe_title = safe_title[:190]
    print(f"  标题: {safe_title}", file=sys.stderr)

    # 第三步: 提取 m3u8 URL 并获取最佳分辨率
    print("[3/3] 提取视频 URL...", file=sys.stderr)
    playlist_url = extract_hls_url(html)
    playlist_url = playlist_url.rstrip('\\')
    print(f"  Playlist: {playlist_url}", file=sys.stderr)

    dl_kwargs = {}
    if proxy:
        dl_kwargs['proxies'] = {'https': proxy, 'http': proxy}
    dl_session = requests.Session(impersonate="chrome136", **dl_kwargs)
    dl_session.headers.update({
        'Origin': page_origin,
        'Referer': page_referer,
        'User-Agent': USER_AGENT
    })
    if page_cookies:
        dl_session.cookies.update(page_cookies)

    best_url, resolution = get_best_quality_url(playlist_url, dl_session, quality=quality)
    if resolution > 0:
        quality_disp = str(quality or 'best')
        print(f"  目标清晰度: {quality_disp}", file=sys.stderr)
        print(f"  选中分辨率: {resolution}p", file=sys.stderr)

    start_seconds = parse_time_to_seconds(clip_start) if clip_start else None
    end_seconds = parse_time_to_seconds(clip_end) if clip_end else None

    if end_seconds is not None and start_seconds is None:
        start_seconds = 0

    if start_seconds is not None and end_seconds is not None and end_seconds <= start_seconds:
        raise ValueError(f"结束时间必须大于开始时间: {clip_start} -> {clip_end}")

    clip_suffix = ''
    if start_seconds is not None or end_seconds is not None:
        start_label = seconds_to_hhmmss(start_seconds or 0).replace(':', '-')
        end_label = seconds_to_hhmmss(end_seconds).replace(':', '-') if end_seconds is not None else 'end'
        clip_suffix = f"_{start_label}-{end_label}"

    if info_only:
        # 只输出信息
        print(f"\n{'='*60}")
        print(f"标题: {safe_title}")
        print(f"分辨率: {resolution}p")
        print(f"Master Playlist: {playlist_url}")
        print(f"最佳流 URL: {best_url}")
        if start_seconds is not None or end_seconds is not None:
            start_disp = seconds_to_hhmmss(start_seconds or 0)
            end_disp = seconds_to_hhmmss(end_seconds) if end_seconds is not None else 'END'
            print(f"时间段: {start_disp} - {end_disp}")
        print(f"{'='*60}")
        print(f"\n可用以下命令下载:")
        ffmpeg_bin = find_ffmpeg()
        header_lines = [
            f'Origin: {page_origin}',
            f'Referer: {page_referer}',
            f'User-Agent: {USER_AGENT}',
        ]
        if cookie_header:
            header_lines.append(f'Cookie: {cookie_header}')
        ffmpeg_headers = '\\r\\n'.join(header_lines) + '\\r\\n'
        cmd = [
            ffmpeg_bin,
            '-headers', ffmpeg_headers
        ]
        cmd.extend(['-i', best_url])
        if start_seconds is not None:
            cmd.extend(['-ss', seconds_to_hhmmss(start_seconds)])
        if end_seconds is not None:
            if start_seconds is None:
                cmd.extend(['-to', seconds_to_hhmmss(end_seconds)])
            else:
                cmd.extend(['-t', str(end_seconds - start_seconds)])
        cmd.extend(['-c', 'copy', '-y', f"{safe_title}{clip_suffix}.mp4"])
        print(' '.join(f'"{x}"' if ' ' in x else x for x in cmd))
        return best_url

    # 下载
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, f"{safe_title}{clip_suffix}.mp4")
    download_with_ffmpeg(
        best_url,
        output_path,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        proxy=proxy,
        origin=page_origin,
        referer=page_referer,
        cookie_header=cookie_header
    )
    return output_path


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='MissAV 视频下载器')
    parser.add_argument('url', nargs='?', default=CONFIG_TARGET_URL, help='视频页面 URL（默认取脚本顶部配置）')
    parser.add_argument('-o', '--output', default='./download', help='输出目录')
    parser.add_argument('-p', '--proxy', default=DEFAULT_PROXY, help='代理地址，默认 http://127.0.0.1:10808')
    parser.add_argument('--html', default=None, help='使用本地 HTML 文件代替网络请求')
    parser.add_argument('--info-only', action='store_true', help='只输出视频链接，不下载')
    parser.add_argument('--start', default=DEFAULT_CLIP_START, help='裁剪开始时间 (HH:MM:SS / MM:SS / 秒数)')
    parser.add_argument('--end', default=DEFAULT_CLIP_END, help='裁剪结束时间 (HH:MM:SS / MM:SS / 秒数)')
    parser.add_argument('--quality', default=DEFAULT_QUALITY, help='清晰度：best(最高分辨率) 或 1080/720/480 等')
    args = parser.parse_args()

    print(f"目标: {args.url}", file=sys.stderr)
    if not args.info_only:
        print(f"输出目录: {args.output}", file=sys.stderr)
        print(f"时间段: {args.start} - {args.end}", file=sys.stderr)
        print(f"清晰度: {args.quality}", file=sys.stderr)
    print(file=sys.stderr)

    try:
        result = download_missav(
            args.url,
            args.output,
            proxy=args.proxy,
            html_file=args.html,
            info_only=args.info_only,
            clip_start=args.start,
            clip_end=args.end,
            quality=args.quality
        )
        print(f"\n完成: {result}")
    except Exception as e:
        print(f"\n错误: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)