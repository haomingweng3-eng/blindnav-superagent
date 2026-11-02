package com.blindnav.app.last10m

/**
 * 调试用人工入口候选（任务书第 22 节的"今天不做复杂投影"版）。
 *
 * 上一版的错误很具体：门框是拿**研究面板 TextView 的 width/height** 乘比例算出来的，
 * 于是（1）框根本不在"画面中央偏下"，而在左上角面板那一小块；（2）不管点屏幕哪儿，
 * 产生的都是同一个框，`EntranceTracker` 永远归并成同一扇 `door-1`，
 * 结果真机上根本造不出第二个候选门 —— 邻门冲突、`MULTIPLE_DOORS` 这些
 * 最该在门口拦住系统的路径，反而永远跑不到。
 *
 * 这一版改成分区按钮：**「标左门 / 标中门 / 标右门」**，框由**当前 CameraX 分析帧的
 * 真实尺寸**换算（[frameWidth]/[frameHeight] 来自 Bitmap，不来自任何 View）。
 * 三个分区在水平方向互不重叠，所以：
 *  - 按两个不同分区 ⇒ 一定得到两个候选门 ⇒ 邻门冲突路径可以被真实触发；
 *  - 按同一分区 ⇒ 归并成同一扇门（这是同一扇门的重复标注，符合预期）。
 *
 * 仍然**不是**检测结果：`source` 一律 `DEBUG_MANUAL_PROPOSAL`，
 * 面板、日志、采集数据三处都要能看出这是人按的。这里也刻意不做触摸点→帧坐标的投影，
 * 那是下一期的事，不要为了演示顺手猜一个。
 */
object DebugDoorProposal {

    /** 画面竖直方向的统一取景带：门一般在招牌下方、地面之上。 */
    const val TOP_NORM = 0.40f
    const val BOTTOM_NORM = 0.90f

    /** 每个分区的水平范围（归一化）。 */
    fun rangeOf(position: DebugDoorPosition): Pair<Float, Float> = when (position) {
        DebugDoorPosition.LEFT -> 0.05f to 0.35f
        DebugDoorPosition.CENTER -> 0.35f to 0.65f
        DebugDoorPosition.RIGHT -> 0.65f to 0.95f
    }

    /**
     * 把分区换算成**帧坐标**的像素框。
     * 帧尺寸不可用时返回 null —— 绝不能退化成一个假想的默认尺寸，
     * 那等于把"不知道画面多大"伪装成"知道"。
     */
    fun box(position: DebugDoorPosition, frameWidth: Int, frameHeight: Int): IntArray? {
        if (frameWidth <= 0 || frameHeight <= 0) return null
        val (x0, x1) = rangeOf(position)
        val left = (frameWidth * x0).toInt()
        val right = (frameWidth * x1).toInt()
        val top = (frameHeight * TOP_NORM).toInt()
        val bottom = (frameHeight * BOTTOM_NORM).toInt()
        if (right <= left || bottom <= top) return null
        return intArrayOf(left, top, right, bottom)
    }

    /** 面板与日志用的一行说明，含真实像素框，方便核对"框是不是落在门上"。 */
    fun describe(position: DebugDoorPosition, frameWidth: Int, frameHeight: Int): String {
        val b = box(position, frameWidth, frameHeight)
            ?: return "$position frame=?x?（还没有可用帧，先打开看路）"
        return "$position frame=${frameWidth}x${frameHeight} box=${b[0]},${b[1]},${b[2]},${b[3]}"
    }
}
