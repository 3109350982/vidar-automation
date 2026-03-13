# Short Video Platform Automation Research Toolkit

# 短视频平台自动化研究工具

------------------------------------------------------------------------

## English Version

### Project Overview

This project is a **browser automation and data collection toolkit**
designed for studying interaction patterns on short‑video platforms.

The system simulates user navigation and interaction through automated
browser control, extracts structured information from public content,
and stores it locally for further analysis or experimentation.

A lightweight web interface is included to manage tasks and view
collected data.

The project focuses on **automation research, web scraping techniques,
and backend system architecture practice**.

------------------------------------------------------------------------

### Key Features

#### Automated Browser Interaction

The system controls a real browser session to simulate user behavior:

-   Navigate video pages
-   Open comment sections
-   Scroll and browse content
-   Perform basic interaction actions

This approach allows realistic interaction experiments without relying
on official APIs.

------------------------------------------------------------------------

#### Intelligent DOM Element Locator

A hybrid selector strategy improves automation stability when page
structures change.

Features:

-   Multi‑selector fallback strategy
-   Visibility filtering
-   Retry and recovery mechanisms
-   Page readiness detection

------------------------------------------------------------------------

#### Comment & Content Extraction

The toolkit extracts structured information from public pages:

-   Username
-   User profile link
-   Comment text
-   IP location (when available)
-   Video description
-   Engagement metrics

The system reconstructs fragmented DOM text nodes and emoji elements to
ensure accurate content extraction.

------------------------------------------------------------------------

#### Local Data Storage System

All collected data is stored locally using SQLite.

Data tables include:

-   Users
-   Videos
-   Task logs
-   Interaction status tracking

The storage layer supports schema migration, deduplication, and
time‑based cleanup.

------------------------------------------------------------------------

#### Task Scheduling System

Automation workflows run through a modular scheduler that manages:

-   Task lifecycle
-   Execution status
-   Retry strategies
-   Logging

This makes it easy to extend the system with new automation modules.

------------------------------------------------------------------------

#### Web Control Panel

A lightweight web interface provides:

-   Task control
-   Status monitoring
-   Recent data display
-   Basic statistics

Users can manage automation tasks without directly modifying code.

------------------------------------------------------------------------

#### Anti‑Detection Strategies

Several techniques help reduce automation detectability:

-   Randomized delays
-   Human‑like typing simulation
-   Controlled scrolling patterns
-   Dynamic interaction timing

These mechanisms improve stability for long‑running automation sessions.

------------------------------------------------------------------------

### Tech Stack

-   Python
-   Playwright
-   FastAPI
-   SQLite
-   HTML / JavaScript

------------------------------------------------------------------------

### Running the Project

Install dependencies:

``` bash
pip install -r requirements.txt
```

Run the server:

``` bash
python run_app.py
```

Open the control panel:

    http://localhost:8000

------------------------------------------------------------------------

### Educational Purpose

This project was developed mainly for:

-   automation experiments
-   web interaction research
-   data extraction techniques
-   backend architecture practice

Please ensure usage complies with platform policies and local
regulations.

------------------------------------------------------------------------

## 中文版本

### 项目简介

本项目是一个用于**短视频平台自动化研究与数据采集实验的工具系统**。

系统通过自动化浏览器控制模拟真实用户的浏览行为，对公开页面内容进行结构化提取，并将数据存储到本地数据库中，方便进行进一步分析与研究。

项目同时提供一个轻量级的 Web
控制界面，用于管理自动化任务和查看数据结果。

本项目主要用于：

-   Web 自动化研究
-   浏览器交互实验
-   数据采集技术学习
-   后端系统架构实践

------------------------------------------------------------------------

### 主要功能

#### 浏览器自动化控制

系统通过自动化浏览器模拟真实用户操作，包括：

-   浏览视频页面
-   打开评论区
-   页面滚动
-   简单互动操作

这种方式可以在不依赖平台 API 的情况下进行自动化研究。

------------------------------------------------------------------------

#### 智能 DOM 元素定位

系统实现了混合选择器策略，提高自动化在页面结构变化时的稳定性。

核心能力：

-   多选择器回退机制
-   可见元素过滤
-   自动重试机制
-   页面加载状态检测

------------------------------------------------------------------------

#### 评论与内容提取

系统可以从公开页面中提取结构化数据，例如：

-   用户昵称
-   用户主页链接
-   评论内容
-   IP 属地信息（若可见）
-   视频文案
-   互动统计信息

系统还对碎片化文本节点和 emoji 元素进行了处理，以提高文本提取准确性。

------------------------------------------------------------------------

#### 本地数据存储

所有数据使用 SQLite 本地数据库进行存储。

主要数据表包括：

-   用户信息
-   视频信息
-   任务日志
-   互动状态记录

系统支持自动建表、数据去重以及按时间清理数据。

------------------------------------------------------------------------

#### 自动任务调度

系统通过任务调度模块管理自动化流程，包括：

-   任务生命周期管理
-   执行状态记录
-   自动重试机制
-   日志管理

该架构使系统易于扩展新的自动化模块。

------------------------------------------------------------------------

#### Web 控制面板

系统提供一个轻量级 Web 界面，用于：

-   启动和停止自动化任务
-   查看系统运行状态
-   浏览采集数据
-   查看基础统计信息

用户无需直接修改代码即可操作系统。

------------------------------------------------------------------------

#### 自动化稳定性策略

系统实现了一些机制以提高自动化稳定性，例如：

-   随机延迟
-   模拟人类输入
-   控制滚动行为
-   动态交互节奏

这些机制可以降低长时间运行时的异常风险。

------------------------------------------------------------------------

### 技术栈

-   Python
-   Playwright
-   FastAPI
-   SQLite
-   HTML / JavaScript

------------------------------------------------------------------------

### 运行方式

安装依赖：

``` bash
pip install -r requirements.txt
```

启动系统：

``` bash
python run_app.py
```

打开控制面板：

    http://localhost:8000

------------------------------------------------------------------------

### 项目用途说明

本项目主要用于：

-   自动化技术学习
-   Web 数据采集研究
-   后端系统架构实践

使用时请遵守相关平台政策与法律法规。
