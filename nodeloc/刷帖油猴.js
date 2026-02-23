// ==UserScript==
// @name         NodeLoc 自动滚动辅助器
// @namespace    https://www.nodeloc.com/
// @version      2.2
// @description  在 NodeLoc 论坛自动向下/向上滚动页面，到达底部时自动反向滚动，方便浏览帖子列表
// @author       DeepSeek-R1
// @match        *://www.nodeloc.com/*
// @match        *://nodeloc.com/*
// @grant        GM_addStyle
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// ==/UserScript==

(function () {
    'use strict';

    // 配置参数（单位改为秒）
    const defaultSettings = {
        scrollSpeed: 2,      // 每帧滚动的像素数（nodeloc 帖子列表较长，稍快）
        scrollInterval: 30,  // 滚动间隔(ms)
        stopInterval: 3,     // 停止滚动的间隔(s)，到底/顶时停顿 3 秒
        threshold: 150,      // 底部/顶部检测阈值(px)
        enableUI: true       // 是否显示控制UI
    };

    // 加载用户设置
    let settings = Object.assign({}, defaultSettings);
    if (GM_getValue('autoScrollSettings')) {
        settings = Object.assign(settings, GM_getValue('autoScrollSettings'));
    }

    // 状态变量
    let scrollDirection = 1;    // 1=向下, -1=向上
    let scrollIntervalId = null;
    let isScrolling = false;
    let isWaiting = false;      // 新增：等待状态标记

    // 创建控制UI
    function createControlUI() {
        // 创建控制面板元素
        const controlPanel = document.createElement('div');
        controlPanel.id = 'autoScrollControl';
        document.body.appendChild(controlPanel);

        // 创建UI元素
        controlPanel.innerHTML = `
<div class="control-header">
    <div class="control-title">自动滚动控制</div>
    <button class="close-btn" id="closeControl">×</button>
</div>

<div class="control-group">
    <label class="control-label">滚动速度: px/帧</label>
    <div class="slider-container">
        <input type="range" id="speedSlider" class="slider" min="1" max="500" value="${settings.scrollSpeed}">
        <div class="value-display" id="speedDisplay">${settings.scrollSpeed}</div>
    </div>
</div>

<div class="control-group">
    <label class="control-label">刷新间隔: ms</label>
    <div class="slider-container">
        <input type="range" id="intervalSlider" class="slider" min="5" max="100" value="${settings.scrollInterval}">
        <div class="value-display" id="intervalDisplay">${settings.scrollInterval}</div>
    </div>
</div>

<div class="control-group">
    <label class="control-label">停止间隔: s</label>
    <div class="slider-container">
        <input type="range" id="stopIntervalSlider" class="slider" min="0" max="600" value="${settings.stopInterval}">
        <div class="value-display" id="stopIntervalDisplay">${settings.stopInterval}</div>
    </div>
</div>

<div class="buttons">
    <button class="btn btn-start" id="startBtn">开始滚动</button>
    <button class="btn btn-stop" id="stopBtn">停止滚动</button>
</div>`;

        // 添加样式
        GM_addStyle(`
#autoScrollControl {
    position: fixed;
    bottom: 20px;
    right: 20px;
    z-index: 9999;
    background: rgba(30, 30, 50, 0.9);
    border-radius: 10px;
    padding: 15px;
    box-shadow: 0 4px 20px rgba(0, 0, 0, 0.4);
    border: 1px solid #444;
    backdrop-filter: blur(5px);
    min-width: 250px;
    color: #fff;
    font-family: Arial, sans-serif;
    transition: transform 0.3s ease;
}

#autoScrollControl:hover {
    transform: translateY(-5px);
}

#autoScrollControl .control-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-bottom: 15px;
    padding-bottom: 10px;
    border-bottom: 1px solid #444;
}

#autoScrollControl .control-title {
    font-size: 18px;
    font-weight: bold;
    color: #ff8a00;
}

#autoScrollControl .close-btn {
    background: none;
    border: none;
    color: #aaa;
    font-size: 20px;
    cursor: pointer;
    transition: color 0.3s;
}

#autoScrollControl .close-btn:hover {
    color: #fff;
}

#autoScrollControl .control-group {
    margin-bottom: 15px;
}

#autoScrollControl .control-label {
    display: block;
    margin-bottom: 8px;
    font-size: 14px;
    color: #a0b3ff;
}

#autoScrollControl .slider-container {
    display: flex;
    align-items: center;
    gap: 10px;
}

#autoScrollControl .slider {
    flex: 1;
    height: 6px;
    -webkit-appearance: none;
    background: rgba(255, 255, 255, 0.1);
    outline: none;
    border-radius: 3px;
}

#autoScrollControl .slider::-webkit-slider-thumb {
    -webkit-appearance: none;
    width: 18px;
    height: 18px;
    border-radius: 50%;
    background: #ff8a00;
    cursor: pointer;
    box-shadow: 0 0 5px rgba(255, 138, 0, 0.7);
}

#autoScrollControl .value-display {
    min-width: 40px;
    text-align: center;
    font-size: 14px;
    font-weight: bold;
    color: #ff8a00;
}

#autoScrollControl .buttons {
    display: flex;
    gap: 10px;
    margin-top: 10px;
}

#autoScrollControl .btn {
    flex: 1;
    padding: 8px 15px;
    border: none;
    border-radius: 6px;
    font-size: 14px;
    font-weight: bold;
    cursor: pointer;
    transition: all 0.3s ease;
}

#autoScrollControl .btn-start {
    background: linear-gradient(to right, #22c1c3, #1a9c9e);
    color: white;
}

#autoScrollControl .btn-stop {
    background: linear-gradient(to right, #e52e71, #c41c5c);
    color: white;
}`);

        // 获取UI元素
        const speedSlider = controlPanel.querySelector('#speedSlider');
        const speedDisplay = controlPanel.querySelector('#speedDisplay');

        const intervalSlider = controlPanel.querySelector('#intervalSlider');
        const intervalDisplay = controlPanel.querySelector('#intervalDisplay');

        const stopIntervalSlider = controlPanel.querySelector('#stopIntervalSlider');
        const stopIntervalDisplay = controlPanel.querySelector('#stopIntervalDisplay');

        const startBtn = controlPanel.querySelector('#startBtn');
        const stopBtn = controlPanel.querySelector('#stopBtn');
        const closeBtn = controlPanel.querySelector('#closeControl');

        // 滑块事件
        speedSlider.addEventListener('input', function () {
            settings.scrollSpeed = parseInt(this.value);
            speedDisplay.textContent = settings.scrollSpeed;
            GM_setValue('autoScrollSettings', settings);

            if (isScrolling) {
                stopAutoScroll();
                startAutoScroll();
            }
        });

        intervalSlider.addEventListener('input', function () {
            settings.scrollInterval = parseInt(this.value);
            intervalDisplay.textContent = settings.scrollInterval;
            GM_setValue('autoScrollSettings', settings);

            if (isScrolling) {
                stopAutoScroll();
                startAutoScroll();
            }
        });

        stopIntervalSlider.addEventListener('input', function () {
            settings.stopInterval = parseInt(this.value);
            stopIntervalDisplay.textContent = settings.stopInterval;
            GM_setValue('autoScrollSettings', settings);

            if (isScrolling) {
                stopAutoScroll();
                startAutoScroll();
            }
        });

        // 按钮事件
        startBtn.addEventListener('click', startAutoScroll);
        stopBtn.addEventListener('click', stopAutoScroll);

        // 关闭按钮
        closeBtn.addEventListener('click', function () {
            controlPanel.style.display = 'none';
            settings.enableUI = false;
            GM_setValue('autoScrollSettings', settings);
        });
    }

    // 开始滚动
    function startAutoScroll() {
        if (scrollIntervalId) {
            stopAutoScroll(); // 先停止现有的滚动
        }

        isScrolling = true;
        isWaiting = false;  // 重置等待状态

        // 获取页面最大滚动位置
        const maxPos = document.documentElement.scrollHeight - window.innerHeight;

        scrollIntervalId = setInterval(() => {
            // 如果正在等待，则跳过本次滚动
            if (isWaiting) return;

            // 获取当前滚动位置
            const currentPos = window.pageYOffset || document.documentElement.scrollTop;

            // 检查是否到达边界
            if (scrollDirection === 1 && currentPos >= maxPos - settings.threshold) {
                scrollDirection = -1; // 到达底部，改为向上
            } else if (scrollDirection === -1 && currentPos <= settings.threshold) {
                scrollDirection = 1; // 到达顶部，改为向下
            }

            // 如果有停止间隔，设置等待状态
            if (settings.stopInterval > 0) {
                isWaiting = true;

                // 设置等待超时后恢复滚动
                setTimeout(() => {
                    isWaiting = false;
                    window.scrollBy({
                        top: settings.scrollSpeed * scrollDirection,
                        behavior: 'instant'
                    });
                }, settings.stopInterval * 1000); // 秒转换为毫秒
            } else {
                // 无停止间隔，直接滚动
                window.scrollBy({
                    top: settings.scrollSpeed * scrollDirection,
                    behavior: 'instant'
                });
            }
        }, settings.scrollInterval);
    }

    // 停止滚动
    function stopAutoScroll() {
        if (scrollIntervalId) {
            clearInterval(scrollIntervalId);
            scrollIntervalId = null;
            isScrolling = false;
            isWaiting = false; // 重置等待状态
        }
    }

    // 注册菜单命令
    GM_registerMenuCommand('开始自动滚动', startAutoScroll);
    GM_registerMenuCommand('停止自动滚动', stopAutoScroll);
    GM_registerMenuCommand('显示控制面板', function () {
        const controlPanel = document.getElementById('autoScrollControl');
        if (controlPanel) {
            controlPanel.style.display = 'block';
            settings.enableUI = true;
            GM_setValue('autoScrollSettings', settings);
        }
    });

    // 初始化
    window.addEventListener('load', function () {
        try {
            createControlUI();
            if (!settings.enableUI) {
                const controlPanel = document.getElementById('autoScrollControl');
                if (controlPanel) {
                    controlPanel.style.display = 'none';
                }
            }
        } catch (e) {
            console.error('自动滚动辅助器初始化错误:', e);
        }
    });
})();