# Check Steam-Link

一个AstrBot插件。
A plugin for AstrBot.

# 功能介绍
## 已实现
- 自动检测对话中出现的steam商店页链接，并返回对应页面的网页截图和摘要信息。
- 更详细的信息解析。
- 检测steam个人主页链接，并返回个人主页截图。
  目前支持的格式如下：
```
https://store.steampowered.com/app/881020/Granblue_Fantasy_Relink/ # 游戏商店页链接
https://steamcommunity.com/id/inori_333/ # 个人主页链接
```
可解析的信息：
```
游戏名称
发行时间
开发商
发行商
游戏类别（保留前五个）
游戏简介
游戏评分
游戏价格
是否支持中文（包括简体中文和繁体中文）
```
## 待实现
- 返回更更详细的商店页文字信息。
- 返回与链接游戏相关的其他信息，比如从SteamDB获取的价格变化等等。
- 支持参数设置，比如是否需要返回截图，截屏的宽度和高度，返回摘要的详细等级等等。

# 使用方法
## 软件依赖
程序依赖无头参数下的Chrome浏览器进行本地截屏，**您的主机需要安装Chrome浏览器**，没有的话去官网装一个。
## 第三方库依赖
程序依赖以下第三方库：
- selenium
- webdriver-manager
- beautifulsoup4

但是，您应该无需手动安装任何第三方库，本插件会自动检测您的环境，并安装缺失的库。
## 前端使用
无需使用任何指令，插件会自动检测对话中出现的steam商店页链接，并返回对应页面的网页截图和摘要信息。
(当然目前也不支持任何指令就是了)

# 使用示例
![使用示例](sample.png)
![使用示例2](sample2.png)

# 支持
[帮助文档](https://github.com/inori-3333/astrbot_plugin_steamshot)
