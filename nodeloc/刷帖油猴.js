// ==UserScript==
// @name         NodeLoc 自动阅读刷能量脚本 (DOM模拟版)
// @namespace    http://tampermonkey.net/
// @version      1.0
// @description  通过模拟真实滚动和解除休眠，自动刷取 Discourse 论坛阅读时长。
// @author       YourName
// @match        https://www.nodeloc.com/*
// @grant        none
// ==/UserScript==

(function() {
    'use strict';

    // ================= 配置区域 =================
    const config = {
        scrollInterval: 4000,     // 每隔几秒向下滚动一次 (毫秒) - 建议3-5秒，太快会被识别为机器人
        baseScrollStep: 400,      // 每次基础滚动像素
        antiIdleInterval: 12000,  // 每隔多少秒发送一次防休眠心跳 (毫秒)
        nextTopicDelay: 6000,     // 到底部后，停留多久跳转下一篇 (毫秒)
        enableAutoNext: true      // 是否开启全自动连续刷帖
    };

    let isBottomReached = false;

    // ================= 核心1：防休眠破解 =================
    // Discourse 只要检测不到鼠标移动或按键，就会停止计时。我们需要定期骗过它。
    function keepAlive() {
        // 伪造真实的鼠标移动事件
        const mouseEvent = new MouseEvent('mousemove', {
            view: window,
            bubbles: true,
            cancelable: true,
            clientX: Math.random() * window.innerWidth,
            clientY: Math.random() * window.innerHeight
        });
        document.dispatchEvent(mouseEvent);

        // 伪造无害的键盘事件 (Shift键)
        const keyEvent = new KeyboardEvent('keydown', {
            view: window, bubbles: true, cancelable: true, keyCode: 16, shiftKey: true
        });
        document.dispatchEvent(keyEvent);

        console.log("[NodeLoc Bot] 已发送防休眠心跳 💓");
    }

    // ================= 核心2：模拟真实阅读滚动 =================
    // 只有帖子进入屏幕(Viewport)，Discourse 才会为该帖计时
    function autoScroll() {
        if (isBottomReached) return;

        // 加入随机数，防止每次滚动的距离完全一样被封号
        const step = config.baseScrollStep + (Math.random() * 150 - 75);
        window.scrollBy({ top: step, left: 0, behavior: 'smooth' });

        // 检测是否到达页面底部
        if ((window.innerHeight + window.scrollY) >= document.body.offsetHeight - 200) {
            console.log("[NodeLoc Bot] 已到达话题底部，准备进行下一步...");
            isBottomReached = true;
            if (config.enableAutoNext) {
                setTimeout(goToNextTopic, config.nextTopicDelay);
            }
        }
    }

    // ================= 核心3：全自动跳转循环 =================
    // 刷完一篇后，自动点击底部推荐的帖子继续刷
    function goToNextTopic() {
        console.log("[NodeLoc Bot] 寻找下一篇话题...");

        // Discourse 底部通常有 .suggested-topics 推荐列表
        const nextLinks = document.querySelectorAll('.suggested-topics .topic-list-item a.title');

        if (nextLinks && nextLinks.length > 0) {
            // 随机点开底部的一个推荐帖子
            const randomIndex = Math.floor(Math.random() * nextLinks.length);
            console.log(`[NodeLoc Bot] 跳转到推荐帖子: ${nextLinks[randomIndex].innerText}`);
            nextLinks[randomIndex].click(); // 触发点击，利用 SPA 无刷新跳转

            // 重置状态，给页面加载留出时间
            setTimeout(() => { isBottomReached = false; }, 4000);
        } else {
            // 如果是在主页，或者没找到推荐帖，就随便找个帖子点进去
            const homeLinks = document.querySelectorAll('.topic-list-item a.title');
            if (homeLinks.length > 0) {
                const randomIndex = Math.floor(Math.random() * homeLinks.length);
                console.log(`[NodeLoc Bot] 从列表跳转: ${homeLinks[randomIndex].innerText}`);
                homeLinks[randomIndex].click();
                setTimeout(() => { isBottomReached = false; }, 4000);
            } else {
                // 如果啥都没找到，强制点击 Logo 回主页重新开始
                const logo = document.querySelector('#site-logo');
                if(logo) logo.click();
                setTimeout(() => { isBottomReached = false; }, 4000);
            }
        }
    }

    // ================= 启动器 =================
    function startBot() {
        console.log("[NodeLoc Bot] 🚀 自动阅读脚本加载成功，正在后台运行...");

        // 开启循环任务
        setInterval(autoScroll, config.scrollInterval);
        setInterval(keepAlive, config.antiIdleInterval);
    }

    // 延迟5秒启动，等待 Discourse 庞大的 Ember 框架和 DOM 渲染完毕
    setTimeout(startBot, 5000);

})();