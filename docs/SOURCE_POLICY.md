# Reddit 来源准入记录

审阅日：2026-10-13（Asia/Shanghai）。用途：本地研究外国游客来华旅行常见问题及可选社区经验，不用于训练、公开转载、旅行站或商业化。来源为S5社区经验；支付、签证/政策、安全健康内容一律未经官方核验。

| 项目 | 记录 |
|---|---|
| 来源 | Reddit，优先仅 r/travelchina；source_id=reddit_travelchina |
| 所有者 | Reddit 平台及各内容作者；公开可见不等于可自动采集 |
| 入口 | 仅明确获准 OAuth API；/r/travelchina/new、/api/info，另获准时可选 /r/travelchina/comments/{post} |
| 默认资格 | 未配置批准记录、OAuth凭据或用途配额；源码默认disabled，零内容请求 |
| 字段 | post/comment IDs、父关系、canonical URL、源时间/编辑时间/观测时间、主题、清洗全文与短片段、版本、run/request/archive/manifest血缘 |
| 不取 | 个人主页、用户名/作者ID派生字段、邮箱/电话/地址等非必要信息、私信、Cookie、会话、付费及私密内容 |
| 原始响应 | API响应可能含公开作者字段，作为短期证据仅本地0600归档，不进入模型、Git或网站；不建立作者表 |
| 保留 | 项目保守上限48小时；安全视图到期立即隐藏；维护循环每小时sync/expire；删除优先物理清除所有相关全文/片段/响应与payload |
| 使用/再分发 | 仅本地研究；未获外部再分发/商业化许可；不推断敏感用户特征或跨平台识别 |
| 缩略输出 | 只给安全视图<=360字片段和聚合；纯引用/广告/注入/重复不作为有价值答案 |
| 网络边界 | 每host>=20秒，5MiB，最多2次retry且计入预算；无代理/跳转/网页或RSSfallback，401/403/429停轮 |

依据以下一手资料审阅，并只记录政策结论，不采集社区正文作为许可测试：

- [Responsible Builder Policy](https://support.reddithelp.com/hc/en-us/articles/42728983564564-Responsible-Builder-Policy)：页面标注2026-10-05更新；要求API明确批准，研究章节将研究访问限定于获准RFR参与者，商业用途另需书面许可。当前项目不能自行认为一般开发者token可用于研究。
- [Data API Terms](https://redditinc.com/policies/data-api-terms)：页面标注2026-07-20修订；访问必须使用规定的Access Info，不得超出获准用途或规避限制。
- [Don't break the site](https://support.reddithelp.com/hc/en-us/articles/360043512931-Don-t-break-the-site)：2026-10-06更新，禁止无授权协议抓取及规避技术限制。
- [Reddit Data API Wiki](https://support.reddithelp.com/hc/en-us/articles/16160319875092-Reddit-Data-API-Wiki)：要求注册OAuth及真实描述UA，删除来源已删内容（包含去标识副本），强烈建议48小时内常规删除用户数据/内容；限额仅适用获资格客户端。本项目采用>=20秒间隔，不把公开100QPM说明当本项目额度。
- [robots.txt](https://www.reddit.com/robots.txt)：2026-10-13通过公开政策检索工具查看；可见版本对公共.json、搜索、评论RSS等有限制。官方API Wiki明确robots用于搜索引擎而非API使用者。robots的Allow条目不授予API或研究许可；本项目无网页/RSS采集路径。

平台政策优先于通用方法论“不可变历史归档”建议：一旦观察到删除，将相关整个响应包移除，即使其中同时包含其他尚存内容。不保存删除全文的匿名副本或embedding。保留的hash、source-local ID与审计元数据不含内容，不用于还原用户信息。

启用来源需要一个完整的访问材料组合：批准本用途和实际机制的记录、匹配的凭据/范围/额度，以及可执行的保留与删除条件。批准或工单编号不能替代OAuth bearer token，也不能自行证明允许评论采集。机器门禁只能检查文件与字段，不能替代操作者核实。若批准只允许数据导出，必须按实际机制开发并重新准入，不得改用未获准OAuth。
