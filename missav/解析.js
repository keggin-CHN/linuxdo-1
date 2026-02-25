// ==UserScript==
// @name         MissAV M3U8 提取器
// @namespace    https://missav.com/
// @version      1.0
// @description  自动提取 MissAV 视频页面的 m3u8 地址，显示在页面上方便复制
// @author       You
// @match        *://missav.com/*
// @match        *://missavtv.com/*
// @match        *://*.missav.com/*
// @match        *://*.missavtv.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_setClipboard
// @connect      *
// @run-at       document-idle
// ==/UserScript==

(function () {
    'use strict';

    // 提取 hlsSource
    function extractHlsUrl() {
        const html = document.documentElement.innerHTML;

        // 方法1: hlsSource 属性
        const m1 = html.match(/"hlsSource"\s*:\s*"([^"]+\.m3u8[^"]*)"/);
        if (m1) return m1[1].replace(/\\u0026/g, '&');

        // 方法2: 任意 m3u8 URL
        const m2 = html.match(/https?:\/\/[^\s"'\\]+\.m3u8[^\s"'\\]*/);
        if (m2) return m2[0];

        return null;
    }

    // 解析 master playlist，返回各分辨率流
    function parseMasterPlaylist(text, baseUrl) {
        const lines = text.trim().split('\n');
        const streams = [];

        for (let i = 0; i < lines.length; i++) {
            if (lines[i].includes('#EXT-X-STREAM-INF')) {
                const resMatch = lines[i].match(/RESOLUTION=(\d+)x(\d+)/);
                const bwMatch = lines[i].match(/BANDWIDTH=(\d+)/);
                if (i + 1 < lines.length && !lines[i + 1].startsWith('#')) {
                    let streamUrl = lines[i + 1].trim();
                    if (!streamUrl.startsWith('http')) {
                        if (streamUrl.startsWith('/')) {
                            const u = new URL(baseUrl);
                            streamUrl = u.origin + streamUrl;
                        } else {
                            streamUrl = baseUrl.substring(0, baseUrl.lastIndexOf('/') + 1) + streamUrl;
                        }
                    }
                    streams.push({
                        width: resMatch ? parseInt(resMatch[1]) : 0,
                        height: resMatch ? parseInt(resMatch[2]) : 0,
                        bandwidth: bwMatch ? parseInt(bwMatch[1]) : 0,
                        url: streamUrl
                    });
                }
            }
        }

        // 按分辨率降序排列
        streams.sort((a, b) => b.height - a.height || b.bandwidth - a.bandwidth);
        return streams;
    }

    // 创建 UI 面板
    function createPanel() {
        const panel = document.createElement('div');
        panel.id = 'missav-m3u8-panel';
        panel.innerHTML = `
            <style>
                #missav-m3u8-panel {
                    position: fixed;
                    top: 10px;
                    right: 10px;
                    z-index: 999999;
                    background: #1a1a2e;
                    color: #eee;
                    border-radius: 10px;
                    padding: 14px 18px;
                    font-family: 'Segoe UI', sans-serif;
                    font-size: 13px;
                    box-shadow: 0 4px 24px rgba(0,0,0,0.5);
                    max-width: 520px;
                    min-width: 320px;
                    transition: opacity 0.3s;
                    border: 1px solid #333;
                }
                #missav-m3u8-panel .panel-header {
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    margin-bottom: 10px;
                }
                #missav-m3u8-panel .panel-title {
                    font-size: 14px;
                    font-weight: bold;
                    color: #e94560;
                }
                #missav-m3u8-panel .panel-close {
                    cursor: pointer;
                    color: #888;
                    font-size: 18px;
                    line-height: 1;
                    padding: 0 4px;
                }
                #missav-m3u8-panel .panel-close:hover { color: #fff; }
                #missav-m3u8-panel .status {
                    color: #aaa;
                    margin-bottom: 8px;
                }
                #missav-m3u8-panel .stream-item {
                    display: flex;
                    align-items: center;
                    gap: 8px;
                    margin: 6px 0;
                    padding: 8px 10px;
                    background: #16213e;
                    border-radius: 6px;
                    border: 1px solid #2a2a4a;
                }
                #missav-m3u8-panel .stream-item.best {
                    border-color: #e94560;
                    background: #1a1a3e;
                }
                #missav-m3u8-panel .stream-res {
                    font-weight: bold;
                    min-width: 55px;
                    color: #0f3460;
                    background: #e94560;
                    color: #fff;
                    padding: 2px 8px;
                    border-radius: 4px;
                    font-size: 12px;
                    text-align: center;
                }
                #missav-m3u8-panel .stream-url {
                    flex: 1;
                    overflow: hidden;
                    text-overflow: ellipsis;
                    white-space: nowrap;
                    color: #8ab4f8;
                    font-size: 11px;
                    cursor: pointer;
                    title: 'Click to copy';
                }
                #missav-m3u8-panel .stream-url:hover {
                    color: #aecbfa;
                    text-decoration: underline;
                }
                #missav-m3u8-panel .btn {
                    cursor: pointer;
                    background: #e94560;
                    color: #fff;
                    border: none;
                    border-radius: 4px;
                    padding: 4px 10px;
                    font-size: 11px;
                    white-space: nowrap;
                }
                #missav-m3u8-panel .btn:hover { background: #c73e54; }
                #missav-m3u8-panel .btn.copied {
                    background: #2ecc71;
                }
                #missav-m3u8-panel .master-section {
                    margin-top: 10px;
                    padding-top: 8px;
                    border-top: 1px solid #333;
                }
                #missav-m3u8-panel .master-label {
                    color: #888;
                    font-size: 11px;
                    margin-bottom: 4px;
                }
                #missav-m3u8-panel .master-url {
                    word-break: break-all;
                    color: #666;
                    font-size: 10px;
                    cursor: pointer;
                }
                #missav-m3u8-panel .master-url:hover { color: #8ab4f8; }
                #missav-m3u8-panel .toggle-btn {
                    position: fixed;
                    top: 10px;
                    right: 10px;
                    z-index: 999998;
                    background: #e94560;
                    color: #fff;
                    border: none;
                    border-radius: 50%;
                    width: 40px;
                    height: 40px;
                    font-size: 20px;
                    cursor: pointer;
                    display: none;
                    box-shadow: 0 2px 10px rgba(0,0,0,0.3);
                    line-height: 40px;
                    text-align: center;
                }
            </style>
            <div class="panel-header">
                <span class="panel-title">🎬 M3U8 提取器</span>
                <span class="panel-close" id="m3u8-close">✕</span>
            </div>
            <div id="m3u8-status" class="status">正在提取...</div>
            <div id="m3u8-streams"></div>
            <div id="m3u8-master" class="master-section" style="display:none"></div>
        `;

        const toggleBtn = document.createElement('button');
        toggleBtn.id = 'missav-m3u8-toggle';
        toggleBtn.className = 'toggle-btn';
        toggleBtn.textContent = '🎬';
        toggleBtn.style.cssText = 'position:fixed;top:10px;right:10px;z-index:999998;background:#e94560;color:#fff;border:none;border-radius:50%;width:40px;height:40px;font-size:20px;cursor:pointer;display:none;box-shadow:0 2px 10px rgba(0,0,0,0.3);';

        document.body.appendChild(panel);
        document.body.appendChild(toggleBtn);

        // 关闭/打开
        document.getElementById('m3u8-close').addEventListener('click', () => {
            panel.style.display = 'none';
            toggleBtn.style.display = 'block';
        });
        toggleBtn.addEventListener('click', () => {
            panel.style.display = 'block';
            toggleBtn.style.display = 'none';
        });

        return panel;
    }

    // 复制到剪贴板
    function copyToClipboard(text, btn) {
        try {
            GM_setClipboard(text, 'text');
        } catch (e) {
            // fallback
            navigator.clipboard.writeText(text).catch(() => {});
        }
        if (btn) {
            const orig = btn.textContent;
            btn.textContent = '已复制';
            btn.classList.add('copied');
            setTimeout(() => {
                btn.textContent = orig;
                btn.classList.remove('copied');
            }, 1500);
        }
    }

    // 渲染流列表
    function renderStreams(streams, masterUrl) {
        const container = document.getElementById('m3u8-streams');
        const statusEl = document.getElementById('m3u8-status');
        const masterEl = document.getElementById('m3u8-master');

        if (!streams || streams.length === 0) {
            statusEl.textContent = '未找到视频流';
            return;
        }

        statusEl.textContent = `找到 ${streams.length} 个视频流:`;

        container.innerHTML = streams.map((s, i) => `
            <div class="stream-item ${i === 0 ? 'best' : ''}">
                <span class="stream-res">${s.height}p${i === 0 ? ' ★' : ''}</span>
                <span class="stream-url" data-url="${s.url}" title="${s.url}">${s.url}</span>
                <button class="btn" data-url="${s.url}">复制</button>
            </div>
        `).join('');

        // 绑定复制事件
        container.querySelectorAll('.btn').forEach(btn => {
            btn.addEventListener('click', () => copyToClipboard(btn.dataset.url, btn));
        });
        container.querySelectorAll('.stream-url').forEach(el => {
            el.addEventListener('click', () => {
                const btn = el.parentElement.querySelector('.btn');
                copyToClipboard(el.dataset.url, btn);
            });
        });

        // Master playlist
        if (masterUrl) {
            masterEl.style.display = 'block';
            masterEl.innerHTML = `
                <div class="master-label">Master Playlist:</div>
                <div class="master-url" title="点击复制">${masterUrl}</div>
            `;
            masterEl.querySelector('.master-url').addEventListener('click', function () {
                copyToClipboard(masterUrl);
                this.style.color = '#2ecc71';
                setTimeout(() => { this.style.color = ''; }, 1500);
            });
        }
    }

    // 请求 master playlist
    function fetchPlaylist(url) {
        return new Promise((resolve, reject) => {
            GM_xmlhttpRequest({
                method: 'GET',
                url: url,
                headers: {
                    'Origin': 'https://missav.com',
                    'Referer': 'https://missav.com/',
                },
                onload: function (resp) {
                    if (resp.status === 200) {
                        resolve(resp.responseText);
                    } else {
                        reject(new Error(`HTTP ${resp.status}`));
                    }
                },
                onerror: function (err) {
                    reject(err);
                }
            });
        });
    }

    // 主逻辑
    async function main() {
        // 判断是否是视频页面（URL 中包含番号格式）
        const path = location.pathname;
        // 视频页面通常是 /ja/xxx-123 或 /xxx-123 格式
        if (path === '/' || path.match(/^\/(genres|actresses|tags|search|new|today-hot|weekly-hot|monthly-hot|uncensored-leak|fc2|makers)\b/)) {
            return; // 非视频页面，不执行
        }

        const panel = createPanel();
        const statusEl = document.getElementById('m3u8-status');

        try {
            // 提取 m3u8 URL
            const masterUrl = extractHlsUrl();
            if (!masterUrl) {
                statusEl.textContent = '❌ 未在页面中找到 m3u8 地址';
                return;
            }

            statusEl.textContent = '正在获取播放列表...';

            // 请求 master playlist
            const playlistText = await fetchPlaylist(masterUrl);
            const streams = parseMasterPlaylist(playlistText, masterUrl);

            if (streams.length === 0) {
                // 不是 master playlist，可能直接就是媒体 playlist
                renderStreams([{ width: 0, height: 0, bandwidth: 0, url: masterUrl }], null);
            } else {
                renderStreams(streams, masterUrl);
            }

        } catch (err) {
            statusEl.textContent = `❌ 提取失败: ${err.message}`;
            console.error('[M3U8 Extractor]', err);
        }
    }

    // 等页面加载完再执行
    if (document.readyState === 'complete') {
        setTimeout(main, 1000);
    } else {
        window.addEventListener('load', () => setTimeout(main, 1000));
    }

})();