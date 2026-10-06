05_14 Waste Plastic–FCC KG — M1 + M2 + M3 SHARED KG
=====================================================

核心原则
--------
仍然只有一套 canonical core KG。
- M1：原料身份/组成 -> 原料性质 -> 反应倾向
- M2：FCC 技术路线 -> 进料准备 -> 反应体系 -> 产品
- M3：路线与反应体系上下文 -> 操作条件 -> 产品与性能

不复制三套 KG，不创建 R1/R2/R3 路线实体。
R1/R2/R3/RELATED 仍然只是 paper/provenance metadata filter。

本版最重要的变化
----------------
1. M1 完全保留 05_12 的 controlled taxonomy：
   - WasteSource 34 类
   - WastePlastic 35 类
   - Polymer 84 类

2. M2 保留原有路线筛选：
   - ALL：全部 1,101 篇
   - R1：76 篇
   - R2：34 篇
   - R3：49 篇
   - R4 / RELATED：942 篇

3. 删除独立 M4。
   原 M4 中与“共加工/协同效应”有关的内容并入 M2 的 R3 补充视图：
   - CO_PROCESSED_WITH
   - HAS_CO_PROCESSING_RATIO
   - SynergyEffect
   - Mechanism

4. M3 对应论文 2.3“反应条件与产品分布”。
   M3 默认分析集不是 1,101 篇，而是 R1+R2+R3 三类明确 FCC 路线论文：159 篇。
   M3 路线按钮：
   - R1+R2+R3：159 篇（默认）
   - R1：76 篇
   - R2：34 篇
   - R3：49 篇
   RELATED 不进入默认的反应条件—产品统计。

M3 科学结构
-----------
A. 路线与反应体系上下文
- WastePlastic
- Polymer
- PyrolysisOil
- FCCFeedstock
- ProcessingRoute
- Catalyst
- Zeolite
- Reactor

B. 操作条件
- Temperature
- CatalystFeedRatio
- ReactionTime
- Pressure
- SpaceVelocity
- CoProcessingRatio
- FeedRate
- FluidizationGasRate

C. 产品与性能
- Product
- ProductProperty
- Yield
- Selectivity
- Conversion

主要关系
--------
- PROCESSED_VIA
- USES_CATALYST
- CONTAINS_ZEOLITE
- USES_REACTOR
- HAS_TEMPERATURE
- HAS_PRESSURE
- HAS_REACTION_TIME
- HAS_SPACE_VELOCITY
- HAS_CATALYST_FEED_RATIO
- HAS_CO_PROCESSING_RATIO
- HAS_FEED_RATE
- HAS_FLUIDIZATION_GAS_RATE
- PRODUCES
- HAS_YIELD
- HAS_SELECTIVITY
- HAS_CONVERSION
- HAS_PRODUCT_PROPERTY
- AFFECTS

为什么不用再建一个 M3 SQLite
----------------------------
04_16_M2_route_layer/m2_route_layer.sqlite 虽然文件名叫 M2 route layer，
但 builder 实际上是把 paper route label 继承到 core KG 的全部 provenance / edge / node。
因此 M3 的 Temperature / CatalystFeedRatio / ReactionTime / Pressure / Product / Yield 等
也可以直接按 R1/R2/R3 过滤。

因此：
- 不重新跑 LLM
- 不复制 core KG
- 不新建 M3 独立图谱
- 直接复用现有 route provenance sidecar

运行
----
第一步（若还没有 route sidecar）：
python .\04_16_build_M2_route_layer_shared_KG_v3_1.py

第二步：
python .\05_14_waste_plastic_fcc_KG_M3_INTERACTION_FIX.py

或：
.\RUN_05_14_M3.ps1

默认地址：
http://127.0.0.1:8051

论文与 interface 对应关系
-------------------------
M1 -> 2.1 原料认知
M2 -> 2.2 废塑料进入 FCC 的技术路线
M3 -> 2.3 反应条件与产品分布

图10可从 M3 的操作条件层统计：
Temperature / CatalystFeedRatio / ReactionTime / Pressure 等区间的路线分布。

图11可从 M3 的“操作条件 <-> 产品与性能”证据共现/直接关系层统计：
条件区间与汽油/石脑油、气体/LPG、轻质烯烃、芳香烃、焦炭等产品类别之间的文献级关联。

05_14 交互修订
------------
1. 所有模块恢复自由 3D force-directed / 重力布局，不再按 M1/M2/M3 列强制固定 x 坐标。
2. 节点颜色统一按 entity type 区分；模块/故事层不覆盖节点颜色。
3. 鼠标悬停节点时 tooltip 使用黑色字体。
4. 右键节点：固定该节点当前 x/y/z 位置；整体视图仍支持旋转、缩放和平移。
5. 修复右侧详情面板 × 关闭按钮。
6. 点击节点后，右侧不再列 Relations，直接显示 Evidence。
7. Evidence 显式显示 Year，并在前端按年份从新到旧排序。
8. M2/M3 路线切换后，关系结构、实体类型数量、具体实体列表和已生成图谱同步按路线刷新。
