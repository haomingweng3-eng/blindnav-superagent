package com.blindnav.app.last10m

/**
 * 视觉推理源的统一接口（任务书第 5 节）。
 *
 * 今晚有三种实现，优先级从高到低：
 *  1. `DeepSeekVisionReasoner` —— 项目里已经在用的 VLM 服务（key 就在 App 自己的 assets 里），
 *     一次调用把目标/门/关系/地面/门类型/按钮/场景摘要一起答回来；
 *  2. `ReplayVisionReasoner` —— 读电脑阶段已经跑出来的真实结果，用于 Demo 回放；
 *  3. `DebugManualVisionReasoner` —— 人在面板上按出来的门与地面，最后的兜底。
 *
 * 三条约束：
 *  - **入参是 JPEG 字节，不是 Bitmap**。这样接口能整套在电脑上测，
 *    Android 侧只负责"把帧压成字节"这一件事。
 *  - **不做 suspend**：项目现在没有协程依赖，为了一个关键字引入一整套依赖不值得；
 *    调用方（编排器）自己放后台线程，本接口按阻塞语义写。
 *  - **失败要能区分"没结果"和"结果不可信"**：网络挂了返回 null（编排器换下一源），
 *    返回了但结构不合格则给 `parseOk=false` 的 UNKNOWN 结果（不许部分采信）。
 */
interface VisionReasoner {

    /** 这个源的来源标记。所有产出的 provenance 都由它决定。 */
    val source: EvidenceSource

    /**
     * @param jpeg 当前帧（已按取景策略压缩）。null 表示这一源不需要图像（回放/人工）。
     * @return null = 这一源这次没给结论（调用方应降级到下一源）；
     *         parseOk=false 的结果 = 给了但不可信，一个字段都不能用。
     */
    fun analyze(jpeg: ByteArray?, frameId: Long, timestampMs: Long,
                target: TargetPoiProfile, context: VisionContext): Last10mVisionResult?

    /** 释放底层连接/会话。允许重复调用。 */
    fun close() {}
}
