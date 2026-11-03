package com.blindnav.app.last10m

import android.graphics.RectF

/**
 * 入口候选产出方的接口（任务书第 22 节）。
 *
 * 现在手机上的门候选来自"用户点一下屏幕"。今晚的公开数据 benchmark 说明：
 * 候选门这一环零训练方案已经能到九成入口命中率，但**没有任何一个方案明显好到
 * 可以直接换掉人工点门**——所以这里只落接口，不落实现，也不接进 CameraActivity。
 * 等真机采回"招牌—门"关系数据、确认了用哪一路，再把实现插进来。
 *
 * 设计上的三条约束：
 *  1. 产出方只给"哪里有门"，不给"是不是它家的门"——归属判断是
 *     [Last10mStateMachine]/VLM 关系那一步的职责，混在一起就没法归因错误。
 *  2. 必须带 sourceFrameId 与 timestampMs：到达判定要检查证据新鲜度，
 *     没有时间戳的证据一律不能用（fuzz 测出过裸减法被时钟回退骗过的坑）。
 *  3. 允许"这一帧没有产出"，用空列表表达，不要用 null 或异常。
 */
data class EntranceProposal(
    val doorId: String,
    val bbox: RectF,
    val score: Float,
    val sourceFrameId: Long,
    val timestampMs: Long,
    /** 产出方标识：owlvit / grounding_dino / vlm / manual_tap，用于分来源统计错误。 */
    val producer: String
) {
    init {
        require(score in 0f..1f) { "score 必须是 0..1，收到 $score" }
        require(timestampMs >= 0) { "timestampMs 不能为负" }
    }
}

/** 一次可取消的产出请求。端侧模型和云端服务都能实现这一个接口。 */
interface EntranceProposalEngine {

    val producerId: String

    /**
     * 对一帧图像产出候选门。实现方**不得**在这里判断目标归属，
     * 也不得输出移动/到达/安全结论；那些属于 [ArrivalAuthority]。
     *
     * @param frame 已经按取景策略裁好的图（benchmark 结论：整图缩到 768 会丢掉小门，
     *              街面带或两路取景能显著改善召回）
     * @param frameId 与 [android.graphics.Bitmap] 一起冻结的帧号，不能事后补
     * @param timestampMs 该帧的时间戳，不是本次调用的当前时间
     */
    fun propose(frame: android.graphics.Bitmap, frameId: Long, timestampMs: Long): List<EntranceProposal>

    /** 释放底层会话/显存。允许重复调用。 */
    fun close() {}
}
