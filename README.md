
# Check Steam-Link V1.7.0

+ 一个AstrBot插件。A plugin for AstrBot.
> 如果您觉得对您有用，请点一个star，我会学猫娘叫。

> 当前版本：v1.7.0

# 功能介绍
## 已实现
自动检测对话中出现的如下内容，并返回对应页面的网页截图和摘要信息：
- steam商店页链接
- steam个人主页链接
- steam创意工坊链接

  目前支持的格式如下：
```
https://store.steampowered.com/app/881020/Granblue_Fantasy_Relink/ # 游戏商店页链接
https://steamcommunity.com/id/inori_333/ # 个人主页链接
https://steamcommunity.com/sharedfiles/filedetails/?id=3472726693 # 创意工坊物品链接
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
- 返回与链接游戏相关的其他信息，比如从SteamDB获取的价格变化等等。
- 支持参数设置，比如是否需要返回截图，截屏的宽度和高度，返回摘要的详细等级等等。
- 支持解析steam个人隐私允许条件下的所有steam好友的状态，比如好友是否在线，好友正在玩什么游戏等等。
- 支持steam登录。
- 支持在搜索steam商店和用户时，返回前x个选项（若有），通过回复指定序号解析指定页面

# 使用方法
## 软件依赖
程序依赖无头参数下的Chrome浏览器进行本地截屏，**您的主机需要安装Chrome浏览器以及对应的ChromeDriver驱动**。
## 第三方库依赖
程序依赖以下第三方库：
- selenium
- webdriver-manager
- requests
- beautifulsoup4

但是，您应该无需手动安装任何第三方库，也无需手动安装chrome驱动，插件会自动检测您的环境，并安装缺失的库和驱动。
即，**唯一的必要条件：您的astrbot运行环境需要有Chrome浏览器。**

## 前端使用
根据收到的steam链接自动解析指定界面，插件会自动检测对话中出现的steam链接，并返回对应页面的网页截图和摘要信息（现仅支持steam商店界面、个人主页界面和创意工坊界面）。
```
使用  /sss  指令搜索steam商店，使用方法: /sss + 游戏名，如: /sss 不/存在的你，和我
使用  /ssu  指令搜索steam用户，使用方法: /ssu + 用户名，如: /ssu m4a1_death-Dawn
```

![使用示例4](sample5.png)
![使用示例5](sample4.png)

# 使用示例
_以下两个示例为v1.0.0版本，当前使用效果请查看更新日志中新的示例。_
![使用示例](sample.png)
![使用示例2](sample2.png)


# 更新记录
## v1.2.0
+ 对steam个人主页链接的监听（返回个人主页截图）
+ 对游戏商店页内容更详细的解析（返回文本）

## v1.3.0
+ 修复了发行商异常换行
+ 自动获取ChromeDriver
+ 异步运行，防止因网络原因卡死astrbot，失败时自动重试

## v1.4.0
+ 修复了打折游戏价格无法正常显示的bug
+ 支持steam网页完整截图

## v1.4.5
+ 支持绕过steam年龄验证界面

## v1.5.0
+ 新增支持steam主页解析功能

## v1.6.0
+ 新增支持steam创意工坊解析功能

## v1.6.1
+ 支持解析steam主页最新动态，并改善排版
+ 支持解析steam个人简介中的链接（之前考虑到可能会有些不良链接，不过现在还是觉得应该问题不大，还是放出来了）
![使用示例3](sample3.png)

## v1.6.3
+ 新增支持steam个人主页封禁记录解析
+ 修复了chrome自动更新导致的chromedriver版本不匹配的问题，如果控制台返回chromedriver版本不匹配，重载插件即可解决

## v1.6.5
+ 修复了steam创意工坊解析的bug
+ 完善Steam创意工坊链接处理功能

## v1.7.0
+ 新增搜索steam商店和搜索steam用户指令


# 支持
[帮助文档](https://github.com/inori-3333/astrbot_plugin_steamshot)
