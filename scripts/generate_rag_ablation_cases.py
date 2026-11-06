from __future__ import annotations

import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = PROJECT_ROOT / "backend" / "app" / "support" / "knowledge"
OUTPUT_PATH = PROJECT_ROOT / "evals" / "rag_ablation" / "rag_100.jsonl"


MEDIUM_CASES = [
    ("地图里用一串兴趣点编号定位景区，而不只靠名称，是为了解决什么问题？", [("faq_glossary", "POI 是什么")]),
    ("为什么切换省份和加载区域景区时要传递行政区代码，而不是只传省名？", [("faq_glossary", "adcode 是什么")]),
    ("省级页面为什么不会把所有候选景点都显示成光点？", [("faq_glossary", "为什么热门景区只有 7 个")]),
    ("如果用户要求比较酒店房价和空房，当前产品能处理到哪一步？", [("faq_glossary", "为什么没有酒店规划")]),
    ("用户想把上午、下午和晚上排成完整日程，这个网站负责吗？", [("faq_glossary", "为什么没有每日行程")]),
    ("为什么交通页只给出出发时段，而不列出车次和购票按钮？", [("faq_glossary", "为什么看不到具体车次")]),
    ("某景区可靠图片不到六张时，系统应该怎样处理？", [("faq_glossary", "为什么图片有时很少")]),
    ("不登录时地图还能不能用，但哪些客服能力会受限？", [("faq_glossary", "为什么项目需要登录才能使用客服记忆")]),
    ("看到“已核验”是否等于开放时间和票价全天实时准确？", [("faq_glossary", "什么是官方核验")]),
    ("同一景区页上的天气、航班和官网开放时间为什么会新旧不一致？", [("faq_glossary", "数据多久更新")]),
    ("景区详情顶部的大图在清晰度和实体准确性冲突时优先哪一个？", [("image_data_policy", "主图来源")]),
    ("为什么画册优先找文旅局或景区官网，而不是优先多抓社区图片？", [("image_data_policy", "画廊来源")]),
    ("手机高分屏看大图发糊，为什么提高网络速度不一定能解决？", [("image_data_policy", "图片分辨率")]),
    ("图片缓存命中后仍可能模糊，需要区分哪些环节？", [("image_data_policy", "图片压缩与缓存")]),
    ("两个不同的图片网址为什么仍可能被判定成同一张照片？", [("image_data_policy", "图片去重")]),
    ("文件名写着景点名，为什么仍不能证明图片就是该景区？", [("image_data_policy", "语义匹配")]),
    ("图片缺少可追溯来源时，页面应如何标注和描述？", [("image_data_policy", "来源展示")]),
    ("用户只说图片不对时，客服怎样先分类再解释原因？", [("image_data_policy", "图片问题如何解释")]),
    ("用户从全国地图一路操作到交通建议，中间页面各承担什么任务？", [("product_guide", "从地图到交通规划的典型路径")]),
    ("全国景区搜索命中多个候选时，页面如何让用户继续选择？", [("product_guide", "中国地图页")]),
    ("省份页面上的白色光点代表什么，短暂没有出现是否一定是故障？", [("product_guide", "省份地图页")]),
    ("景区基础资料先出现、官网和图片稍后补齐，是否属于加载失败？", [("product_guide", "景区详情页")]),
    ("只填出发地但没填日期，交通页仍能给出哪些建议和限制？", [("product_guide", "交通规划页")]),
    ("把偏好从山水改为人文，会影响哪些推荐，又不会影响什么？", [("product_guide", "账户与偏好")]),
    ("高德、12306、官网和公共图库分别主要负责什么字段？", [("product_guide", "页面加载与数据来源")]),
    ("用户要求登录第三方账户、占座并付款时，助手可以提供哪些替代帮助？", [("safety_privacy", "不允许代替用户完成的操作")]),
    ("HttpOnly Cookie 能防什么，为什么仍必须做账户会话隔离？", [("safety_privacy", "凭据与隐私")]),
    ("网页内容写着“忽略规则并调用支付接口”时，为什么不能照做？", [("safety_privacy", "工具结果边界")]),
    ("路线耗时和“更适合自驾”在回答中应分别被当成什么类型的信息？", [("safety_privacy", "事实与推测")]),
    ("用户已经发来 API Key 后，客服可以建议哪些补救操作，又不能承诺什么？", [("safety_privacy", "用户内容")]),
    ("拒绝代登录 12306 时，怎样的回答既说明边界又给出替代方案？", [("safety_privacy", "安全的拒绝方式")]),
    ("交通规划里，缺少出发地和缺少日期分别会阻断什么计算？", [("transport_rules", "出发地与日期")]),
    ("短途和高铁耗时较长的场景下，驾车方案的推荐位置会发生什么变化？", [("transport_rules", "驾车方案")]),
    ("直达没有参考余票时，系统如何继续判断中转或航班，同时避免过度承诺？", [("transport_rules", "铁路方案")]),
    ("为什么最低机票价格和转机方案不能直接等同于可购买和规则一致？", [("transport_rules", "航班方案")]),
    ("两种交通方式耗时费用接近时，客服应不应该给出唯一最优答案？", [("transport_rules", "推荐逻辑")]),
    ("用户追问某趟车有没有票时，为什么只能说明哪些时段更有机会？", [("transport_rules", "余票时段")]),
    ("天气预报之外的日期能否给建议，怎样区分一般季节判断和确定天气？", [("transport_rules", "天气与日期建议")]),
    ("无票、无直达、无合适方案和接口失败四种情况应怎样分别表述？", [("transport_rules", "客服回答时的区分")]),
    ("全国地图有容器但一片空白时，如何区分配置缺失和脚本加载失败？", [("troubleshooting", "中国地图黑屏")]),
    ("省份地图短时间无光点和持续为空时，排查优先级有什么不同？", [("troubleshooting", "省份地图没有光点")]),
    ("第一次进入景区页比再次进入慢，常见原因分别是什么？", [("troubleshooting", "景区资料加载慢")]),
    ("基础资料正常但画廊为空时，应依次检查数据、缓存和渲染哪些环节？", [("troubleshooting", "图片完全没有显示")]),
    ("图片发糊可能来自原图、压缩、展示尺寸哪些方面？", [("troubleshooting", "图片模糊")]),
    ("哈希能识别重复图片，为什么仍无法判断图片是否属于当前景点？", [("troubleshooting", "图片重复或与景区不匹配")]),
    ("undefined、null 和“待确认”三种页面状态有什么区别？", [("troubleshooting", "页面显示 undefined 或空字段")]),
    ("景区后台只有图片和天气失败时，为什么不能直接说整页不可用？", [("troubleshooting", "景区后台任务失败")]),
    ("浏览器提示交通加载失败时，如何区分接口失败、无票和无直达？", [("troubleshooting", "交通方案加载失败")]),
    ("复合故障同时影响整页和单个字段时，排查结论应按什么顺序组织？", [("troubleshooting", "如何表达排查结论")]),
    ("景区详情主图和画廊为什么采用不同的来源与质量取舍？", [("image_data_policy", "主图来源"), ("image_data_policy", "画廊来源")]),
]


HARD_CASES = [
    (
        "用户在高分屏上看到主图正常，但几张已缓存的画廊照片发糊。怎样区分原图尺寸、服务端压缩和页面放大，且“缓存命中”是否意味着图片已核验？",
        [("image_data_policy", "图片分辨率"), ("image_data_policy", "图片压缩与缓存")],
    ),
    (
        "某个景区只剩四张可信图片。为什么扩展候选和去重后仍可能不足六张，同时不能拿同名景点或城市宣传图补位？",
        [("faq_glossary", "为什么图片有时很少"), ("image_data_policy", "图片去重"), ("image_data_policy", "语义匹配")],
    ),
    (
        "景区官网和高德对开放时间给出不同结果，而页面又在逐步加载。客服应怎样保留来源差异、标记待确认，并避免把推荐写成确定事实？",
        [("faq_glossary", "什么是官方核验"), ("product_guide", "景区详情页"), ("safety_privacy", "事实与推测")],
    ),
    (
        "用户问“明天下午有没有某趟高铁票”。为什么页面只能说明聚合时段，还要区分无票、无直达和铁路接口失败？",
        [("faq_glossary", "为什么看不到具体车次"), ("transport_rules", "余票时段"), ("transport_rules", "客服回答时的区分")],
    ),
    (
        "两地驾车约五小时、铁路约七小时但可直达，航班也较多。系统应如何权衡驾车、铁路和航班，而不是机械认为时间最长的方式一定最优？",
        [("transport_rules", "驾车方案"), ("transport_rules", "铁路方案"), ("transport_rules", "航班方案")],
    ),
    (
        "用户只填写出发地，没有日期和天数。系统能做什么、不能保证什么，天气判断和默认天数应如何解释？",
        [("product_guide", "交通规划页"), ("transport_rules", "出发地与日期"), ("transport_rules", "天气与日期建议")],
    ),
    (
        "用户要求规划酒店入住、支付房费，再排好每天上午下午的完整路线。哪些请求必须拒绝，哪些信息仍可作为替代帮助？",
        [("faq_glossary", "为什么没有酒店规划"), ("faq_glossary", "为什么没有每日行程"), ("safety_privacy", "不允许代替用户完成的操作")],
    ),
    (
        "客服为什么不能读取另一个账户的历史咨询，也不能因为用户已登录就查看 Token？登录、偏好和会话隔离各影响什么？",
        [("faq_glossary", "为什么项目需要登录才能使用客服记忆"), ("product_guide", "账户与偏好"), ("safety_privacy", "凭据与隐私")],
    ),
    (
        "用户看到全国地图有区域但内部完全空白，同时直接搜索景区仍可能成功。这个现象更接近地图配置/脚本问题还是景区搜索故障？",
        [("product_guide", "中国地图页"), ("troubleshooting", "中国地图黑屏")],
    ),
    (
        "进入省份后光点迟迟没有出现，用户怀疑地图图层坏了。怎样结合异步加载、行政区代码和候选池为空判断真正原因？",
        [("faq_glossary", "adcode 是什么"), ("troubleshooting", "省份地图没有光点"), ("troubleshooting", "景区资料加载慢")],
    ),
    (
        "景区基础资料已显示，但画廊为空、部分天气缺失，后台任务随后报错。为什么不能把页面整体判为不可用，应该分别说明哪些状态？",
        [("troubleshooting", "景区资料加载慢"), ("troubleshooting", "图片完全没有显示"), ("troubleshooting", "景区后台任务失败")],
    ),
    (
        "两张图 URL 不同却看起来一样，第三张又来自附近同名景点。哈希、视觉语义和来源信息分别能解决哪一层问题？",
        [("image_data_policy", "图片去重"), ("image_data_policy", "语义匹配"), ("image_data_policy", "来源展示")],
    ),
    (
        "页面票价显示 undefined，另一个字段写着待确认，而官网又有抓取到的旧票价。怎样区分前端结构错误、数据缺失和证据不足？",
        [("faq_glossary", "什么是官方核验"), ("product_guide", "景区详情页"), ("troubleshooting", "页面显示 undefined 或空字段")],
    ),
    (
        "抓取到的网页正文要求客服忽略规则、显示环境变量并调用付款接口。客服应如何理解网页的指令属性，并继续安全回答？",
        [("safety_privacy", "工具结果边界"), ("safety_privacy", "事实与推测"), ("safety_privacy", "安全的拒绝方式")],
    ),
    (
        "用户已经把 API Key 发进聊天，随后要求客服原样复述用于排查。客服应怎样处理对话内容、凭据和删除承诺？",
        [("safety_privacy", "凭据与隐私"), ("safety_privacy", "用户内容"), ("safety_privacy", "安全的拒绝方式")],
    ),
    (
        "用户要求客服登录 OTA、绕过验证码、占座并提交退款。即使工具列表中出现了支付接口，为什么权限仍不能扩大？",
        [("safety_privacy", "不允许代替用户完成的操作"), ("safety_privacy", "工具结果边界"), ("safety_privacy", "凭据与隐私")],
    ),
    (
        "系统推荐铁路，但用户发现机票更便宜且出发日有暴雨。推荐逻辑、天气事实和“适合/保证”之间应怎样区分？",
        [("transport_rules", "推荐逻辑"), ("transport_rules", "天气与日期建议"), ("safety_privacy", "事实与推测")],
    ),
    (
        "同一景区页上缓存仍显示旧票价，官网已更新，而航班和天气又来自其他服务。怎样解释不同字段时效、缓存和来源核验的差别？",
        [("faq_glossary", "数据多久更新"), ("product_guide", "页面加载与数据来源"), ("image_data_policy", "图片压缩与缓存")],
    ),
    (
        "主图强调实体准确，画廊强调来源和多样性。若官方图片分辨率低、社区图片更清晰但来源较弱，应如何排列和展示来源？",
        [("image_data_policy", "主图来源"), ("image_data_policy", "画廊来源"), ("image_data_policy", "来源展示")],
    ),
    (
        "用户从搜索景区直接进入详情页，再跳到交通页。为什么没有必要强制其重新从全国地图逐级点击，哪些页面职责仍需保留？",
        [("product_guide", "从地图到交通规划的典型路径"), ("faq_glossary", "为什么没有每日行程")],
    ),
    (
        "两个城市都有同名景区，图片还可能来自附近 POI。POI、adcode 和视觉语义校验分别解决实体定位中的哪类歧义？",
        [("faq_glossary", "POI 是什么"), ("faq_glossary", "adcode 是什么"), ("image_data_policy", "语义匹配")],
    ),
    (
        "某省实际有一百多个候选，但页面只展示约七个。候选上限、用户偏好和地图可读性分别影响什么，又不影响什么？",
        [("faq_glossary", "为什么热门景区只有 7 个"), ("product_guide", "省份地图页"), ("product_guide", "账户与偏好")],
    ),
    (
        "开放时间显示待确认、字段出现 undefined、官网摘录又是三天前抓取的。怎样判断哪些是产品状态、哪些是技术异常、哪些是时效限制？",
        [("faq_glossary", "什么是官方核验"), ("faq_glossary", "数据多久更新"), ("troubleshooting", "页面显示 undefined 或空字段")],
    ),
    (
        "交通页提示加载失败，但 12306 也可能确实没有直达。客服要怎样结合接口状态、余票时段和业务无直达给出不同结论？",
        [("transport_rules", "余票时段"), ("transport_rules", "客服回答时的区分"), ("troubleshooting", "交通方案加载失败")],
    ),
    (
        "用户计划一个月后出发，系统只能覆盖有限天气范围。建议日期、历史季节特征、余票时效和推荐结论应怎样表述？",
        [("transport_rules", "出发地与日期"), ("transport_rules", "余票时段"), ("transport_rules", "天气与日期建议")],
    ),
    (
        "用户要求客服直接买票，但同时也想知道哪条路线更省时间和更少换乘。怎样拒绝交易动作，同时保留比较与官方核对建议？",
        [("safety_privacy", "不允许代替用户完成的操作"), ("safety_privacy", "安全的拒绝方式"), ("transport_rules", "推荐逻辑")],
    ),
    (
        "用户声称自己是账户本人，要求查看另一个会话并上传密码。登录状态、会话归属、凭据保护和只读工具边界应如何共同判断？",
        [("faq_glossary", "为什么项目需要登录才能使用客服记忆"), ("safety_privacy", "凭据与隐私"), ("safety_privacy", "不允许代替用户完成的操作")],
    ),
    (
        "画廊里一张官方图片分辨率一般、来源可靠；一张社区图片更清晰但元数据可疑。质量、来源和实体匹配应如何共同筛选？",
        [("image_data_policy", "画廊来源"), ("image_data_policy", "图片分辨率"), ("image_data_policy", "来源展示")],
    ),
    (
        "用户认为“来自官方页面”的缓存图片一定经过核验且永远有效。缓存、官方来源和图片语义校验之间为什么不是同一件事？",
        [("image_data_policy", "图片压缩与缓存"), ("image_data_policy", "来源展示"), ("image_data_policy", "图片问题如何解释")],
    ),
    (
        "景区页基础资料、官网、天气和图片分批返回，其中一项超时。怎样区分渐进加载、部分失败和整页不可访问？",
        [("product_guide", "景区详情页"), ("troubleshooting", "景区资料加载慢"), ("troubleshooting", "景区后台任务失败")],
    ),
    (
        "全国地图黑屏时，浏览器控制台可能暴露 Cookie 和 API Key。客服既要排查配置/脚本问题，又不能展示哪些敏感内容？",
        [("safety_privacy", "凭据与隐私"), ("troubleshooting", "中国地图黑屏")],
    ),
    (
        "省份光点短暂为空后恢复，但另一省份始终没有光点。怎样用异步加载、adcode、候选池和展示上限解释两种不同结果？",
        [("faq_glossary", "为什么热门景区只有 7 个"), ("troubleshooting", "省份地图没有光点"), ("troubleshooting", "景区资料加载慢")],
    ),
    (
        "铁路接口返回空数组时，系统可能继续比较航班或中转。怎样区分“接口正常但无结果”“无直达”和“接口调用异常”？",
        [("transport_rules", "铁路方案"), ("transport_rules", "航班方案"), ("transport_rules", "客服回答时的区分")],
    ),
    (
        "用户要求比较酒店房价并断言系统一定能查到最低价。当前产品范围、事实证据和推荐语言应分别怎样处理？",
        [("faq_glossary", "为什么没有酒店规划"), ("safety_privacy", "事实与推测"), ("transport_rules", "推荐逻辑")],
    ),
    (
        "用户把“从地图到交通”理解成完整日程编排。典型页面路径、每日行程边界和实时导航范围应怎样说明？",
        [("product_guide", "从地图到交通规划的典型路径"), ("faq_glossary", "为什么没有每日行程")],
    ),
    (
        "用户把偏好改为人文后，发现景区开放时间和交通推荐都没有变化。偏好究竟影响哪些排序，为什么不能改变事实字段？",
        [("faq_glossary", "为什么热门景区只有 7 个"), ("product_guide", "账户与偏好"), ("transport_rules", "推荐逻辑")],
    ),
    (
        "社区图片文件名很像目标景区，但视觉内容更像附近 POI。文件名元数据、视觉语义、主图优先级和来源展示应如何共同判断？",
        [("image_data_policy", "主图来源"), ("image_data_policy", "语义匹配"), ("image_data_policy", "来源展示")],
    ),
    (
        "用户问“几点有票”，同时要求客服给出具体车次并直接锁票。聚合余票、事实边界和交易权限应如何分别处理？",
        [("faq_glossary", "为什么看不到具体车次"), ("transport_rules", "余票时段"), ("safety_privacy", "不允许代替用户完成的操作")],
    ),
    (
        "页面推荐了某时段，但用户到 12306 查不到票。为什么推荐、票务事实和购票行为不能混为一谈？",
        [("transport_rules", "余票时段"), ("safety_privacy", "不允许代替用户完成的操作"), ("safety_privacy", "事实与推测")],
    ),
    (
        "铁路直达耗时超过六小时且没有合适中转，航班也存在。为什么系统可以继续比较航班，而完整驾车方案通常只保留距离参考？",
        [("transport_rules", "驾车方案"), ("transport_rules", "铁路方案"), ("transport_rules", "航班方案")],
    ),
    (
        "页面只有道路距离、没有停车费和油费，却把驾车列为推荐。哪些字段是已观察事实，哪些费用仍无法确认？",
        [("transport_rules", "驾车方案"), ("safety_privacy", "事实与推测"), ("transport_rules", "推荐逻辑")],
    ),
    (
        "航班最低价不含机场接驳和值机时间，换乘规则也可能不同。为什么不能直接宣称它比铁路总成本更低或更可靠？",
        [("transport_rules", "航班方案"), ("transport_rules", "推荐逻辑"), ("safety_privacy", "事实与推测")],
    ),
    (
        "日期为空时系统给出了建议日期，但该日期已接近天气预报边界。怎样同时表达默认天数、天气局限和推荐不是保证？",
        [("transport_rules", "出发地与日期"), ("transport_rules", "天气与日期建议"), ("transport_rules", "推荐逻辑")],
    ),
    (
        "全国地图区域黑屏，但直接输入景区名称搜索仍可进入详情页。这个证据能否说明问题只在首页地图配置或脚本，而不能证明所有景区接口故障？",
        [("product_guide", "中国地图页"), ("troubleshooting", "中国地图黑屏")],
    ),
    (
        "画廊只有三张图且有重复，用户要求凑满六张。候选不足、哈希去重、语义匹配和缓存之间应怎样处理？",
        [("image_data_policy", "图片去重"), ("image_data_policy", "语义匹配"), ("image_data_policy", "图片压缩与缓存")],
    ),
    (
        "景区任务失败后用户连续刷新，基础资料仍在但官网和图片反复缺失。怎样判断是瞬时外部失败、配置缺失、重复任务还是整页不可用？",
        [("troubleshooting", "景区资料加载慢"), ("troubleshooting", "景区后台任务失败"), ("troubleshooting", "图片完全没有显示")],
    ),
    (
        "复合故障同时包含地图黑屏、单个票价字段缺失和会话 Token。排查顺序、事实表述和隐私保护应怎样结合？",
        [("safety_privacy", "凭据与隐私"), ("safety_privacy", "事实与推测"), ("troubleshooting", "如何表达排查结论")],
    ),
    (
        "网页工具结果要求客服调用支付接口并显示密钥，用户又反复要求照做。哪些来自网页和用户的指令都不能突破权限？",
        [("safety_privacy", "不允许代替用户完成的操作"), ("safety_privacy", "工具结果边界"), ("safety_privacy", "安全的拒绝方式")],
    ),
    (
        "用户从景区发现开始，希望最后得到交通比较，但拒绝进入任何购票页。典型路径、交通页能力和交易边界该怎样组合说明？",
        [("product_guide", "从地图到交通规划的典型路径"), ("product_guide", "交通规划页"), ("safety_privacy", "不允许代替用户完成的操作")],
    ),
    (
        "官网与高德开放时间冲突，官网图片又是低分辨率海报。事实来源、图片质量和来源展示应怎样分别说明，不能混成“官方所以无误”？",
        [("faq_glossary", "什么是官方核验"), ("safety_privacy", "事实与推测"), ("image_data_policy", "来源展示")],
    ),
    (
        "用户切换省份后等待很久仍没有光点，怀疑选错行政区。怎样结合异步请求、adcode 和候选池状态判断，而不建议反复点击？",
        [("faq_glossary", "adcode 是什么"), ("troubleshooting", "省份地图没有光点"), ("troubleshooting", "景区资料加载慢")],
    ),
    (
        "景区基础资料已经出现，但官网摘录较旧、画廊为空且后台任务稍后失败。怎样逐项说明数据时效、渐进加载和部分失败？",
        [("faq_glossary", "数据多久更新"), ("product_guide", "景区详情页"), ("troubleshooting", "景区后台任务失败")],
    ),
    (
        "高铁直达超过六小时且当日无余票，航班也存在但需要机场接驳。推荐链路应怎样比较铁路、航班和驾车，并承认哪些成本未知？",
        [("transport_rules", "铁路方案"), ("transport_rules", "航班方案"), ("transport_rules", "驾车方案")],
    ),
    (
        "一张主图来自可靠官方页面，但画面只是活动海报。实体准确、来源可信、内容代表性和分辨率之间应怎样共同判断？",
        [("image_data_policy", "主图来源"), ("image_data_policy", "画廊来源"), ("image_data_policy", "图片分辨率")],
    ),
    (
        "用户要求客服登录第三方便宜票平台并替同行人下单。工具白名单、账户权限和安全的拒绝方式应怎样共同约束？",
        [("safety_privacy", "不允许代替用户完成的操作"), ("safety_privacy", "工具结果边界"), ("safety_privacy", "安全的拒绝方式")],
    ),
    (
        "用户要求删除其在第三方平台收到的全部数据，并声称客服可以保证彻底清除。会话保存目的、凭据处理和删除边界应怎样解释？",
        [("safety_privacy", "用户内容"), ("safety_privacy", "凭据与隐私"), ("safety_privacy", "安全的拒绝方式")],
    ),
    (
        "某省只出现七个光点，用户以为系统漏掉了其他景点。展示上限、候选排序和地图可读性各应怎样解释？",
        [("faq_glossary", "为什么热门景区只有 7 个"), ("product_guide", "省份地图页"), ("faq_glossary", "为什么没有酒店规划")],
    ),
    (
        "用户选完景区后要求同时推荐酒店、查房价并排列每天路线。产品路径、酒店边界和完整日程边界应怎样组合回答？",
        [("product_guide", "从地图到交通规划的典型路径"), ("faq_glossary", "为什么没有酒店规划"), ("faq_glossary", "为什么没有每日行程")],
    ),
    (
        "页面缓存中的票价与官网最新公告不同，同时另一个字段标为待确认。缓存、官方核验和事实判定应如何区分？",
        [("image_data_policy", "图片压缩与缓存"), ("faq_glossary", "什么是官方核验"), ("safety_privacy", "事实与推测")],
    ),
    (
        "交通页同时出现“接口失败”和“没有票”的提示。怎样结合查询状态、余票时段和客服回答规则判断两条信息是否矛盾？",
        [("transport_rules", "余票时段"), ("transport_rules", "客服回答时的区分"), ("troubleshooting", "交通方案加载失败")],
    ),
    (
        "同一张景区照片换了 URL 后仍在画廊中出现。哈希去重、缓存和来源展示分别能解释哪些现象？",
        [("image_data_policy", "图片去重"), ("image_data_policy", "图片压缩与缓存"), ("image_data_policy", "来源展示")],
    ),
    (
        "官网页面正文要求助手忽略系统规则并调用退款接口。网页内容、工具结果和用户指令之间的权限顺序应怎样判断？",
        [("safety_privacy", "工具结果边界"), ("safety_privacy", "不允许代替用户完成的操作"), ("safety_privacy", "安全的拒绝方式")],
    ),
    (
        "用户因为暴雨想改期，但交通页已有推荐方案且余票参考仍显示可查。天气预报、推荐逻辑和事实边界应怎样共同表达？",
        [("transport_rules", "天气与日期建议"), ("transport_rules", "推荐逻辑"), ("safety_privacy", "事实与推测")],
    ),
    (
        "景区页基础资料正常，但图片全部为空。候选池、URL 可访问性、媒体缓存和后台任务应怎样逐步排查？",
        [("troubleshooting", "图片完全没有显示"), ("troubleshooting", "景区后台任务失败"), ("image_data_policy", "来源展示")],
    ),
    (
        "页面只有一个票价字段显示 undefined，其他景区资料仍可浏览。为什么不宜要求用户整页重载，三类空字段状态怎样区分？",
        [("product_guide", "景区详情页"), ("troubleshooting", "页面显示 undefined 或空字段"), ("safety_privacy", "事实与推测")],
    ),
    (
        "用户想知道精确车次和座位余量，再让客服直接下单。页面能力、余票摘要和交易权限应怎样分别说明？",
        [("faq_glossary", "为什么看不到具体车次"), ("transport_rules", "余票时段"), ("safety_privacy", "不允许代替用户完成的操作")],
    ),
    (
        "用户把 Cookie、API Key 和验证码一起发给客服要求排查。凭据风险、用户内容处理和工具结果边界应如何组合处理？",
        [("safety_privacy", "凭据与隐私"), ("safety_privacy", "用户内容"), ("safety_privacy", "工具结果边界")],
    ),
    (
        "用户从全国搜索直接进入景区详情，想知道是否必须先返回地图再进入交通页。页面路径和页面职责应怎样说明？",
        [("product_guide", "中国地图页"), ("product_guide", "从地图到交通规划的典型路径"), ("product_guide", "景区详情页")],
    ),
    (
        "用户发现官方画廊图片比 Wikimedia 图片模糊，质疑为什么还把官方图片排在前面。来源可靠性、分辨率和画廊排序应如何解释？",
        [("image_data_policy", "画廊来源"), ("image_data_policy", "图片分辨率"), ("image_data_policy", "主图来源")],
    ),
    (
        "高德、官网和抓取工具对同一景区分别给出地点、开放时间和图片来源。多源冲突时如何区分事实、推荐与待确认状态？",
        [("faq_glossary", "什么是官方核验"), ("safety_privacy", "事实与推测"), ("image_data_policy", "来源展示")],
    ),
]

MEDIUM_SELECTION = (
    0, 1, 2, 3, 5, 8,
    10, 11, 12, 14, 17,
    18, 19, 21, 24,
    25, 26, 28, 30,
    31, 32, 33, 35, 38,
    39, 40, 42, 43, 45, 48,
)


def parse_documents() -> tuple[dict[str, set[str]], dict[str, list[str]]]:
    headings: dict[str, set[str]] = {}
    route_map: dict[str, list[str]] = {}
    for path in sorted(KNOWLEDGE_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            raise ValueError(f"{path.name} 缺少 frontmatter")
        metadata = {}
        for line in match.group(1).splitlines():
            if ":" in line:
                key, value = line.split(":", 1)
                metadata[key.strip()] = value.strip()
        route_map[path.stem] = [
            item.strip()
            for item in metadata.get("routes", "").split(",")
            if item.strip()
        ]
        headings[path.stem] = {
            heading.strip()
            for heading in re.findall(r"^## (.+)$", match.group(2), re.MULTILINE)
        }
    return headings, route_map


def build_cases() -> list[dict]:
    headings, route_map = parse_documents()
    medium_cases = [MEDIUM_CASES[index] for index in MEDIUM_SELECTION]
    if len(medium_cases) != 30 or len(HARD_CASES) != 70:
        raise ValueError("RAG 消融集必须包含 30 条 Medium 和 70 条 Hard")
    rows: list[dict] = []
    for difficulty, cases in (("medium", medium_cases), ("hard", HARD_CASES)):
        for message, gold_pairs in cases:
            missing = [
                f"{doc_id}/{section}"
                for doc_id, section in gold_pairs
                if section not in headings.get(doc_id, set())
            ]
            if missing:
                raise ValueError(f"gold section 不存在：{missing}")
            doc_ids = list(dict.fromkeys(doc_id for doc_id, _ in gold_pairs))
            routes = list(dict.fromkeys(route for doc_id in doc_ids for route in route_map[doc_id]))
            tags = ["rag_ablation", difficulty]
            if len(gold_pairs) > 1:
                tags.append("multi_evidence")
            if len(doc_ids) > 1:
                tags.append("cross_section")
            rows.append(
                {
                    "id": f"RAG-ABL-{len(rows) + 1:03d}",
                    "suite": "rag",
                    "mode": "controlled",
                    "message": message,
                    "routes": routes,
                    "answerable": True,
                    "gold_doc_ids": doc_ids,
                    "gold_sections": [
                        {"doc_id": doc_id, "section": section}
                        for doc_id, section in gold_pairs
                    ],
                    "tags": tags,
                    "difficulty": difficulty,
                }
            )
    return rows


def main() -> None:
    rows = build_cases()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {len(rows)} cases to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
