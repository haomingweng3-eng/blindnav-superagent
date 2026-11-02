package com.blindnav.app.last10m

import org.junit.Test

/**
 * 第 15 节：用真实连续帧测 EntranceTracker。
 *
 * 数据：BLV-Road-Nav-Accessibility 的 30 段行走视频抽帧（150 帧），
 * 每帧的门框由本地 OWL-ViT 检测产生（Python 只出检测框，不做跟踪）。
 * 跟踪全部走手机在用的同一个 [EntranceTracker]。
 *
 * 时间戳按 frame_index / 30fps 推算（BLV 仓库只给了抽好的帧，没给真实时间戳），
 * 所以这里的"超时/间隔"结论是**相对**的：能看出阈值是不是明显不合理，
 * 不能当成精确标定。帧间隔实测 33–433 ms，而 frame index 差最大 13 > maxGapFrames=6。
 *
 * 本测试只提建议，不改生产参数。
 */
class EntranceTrackerSequenceTest {

    private data class Frame(val frameIndex: Long, val tsMs: Long, val doors: List<IntArray>)
    private data class Seq(val id: String, val frames: List<Frame>)

    /**
     * 读 TSV 而不是 JSON：JVM 单元测试里没有可用的 org.json
     * （android.jar 里的 org.json 在 unit test 中是抛异常的桩，
     *  生产 VlmFacts 也正是因此才不 import 它）。
     */
    private fun load(): List<Seq> {
        val text = javaClass.classLoader!!.getResourceAsStream("last10m/blv_sequences.tsv")!!
            .bufferedReader().readLines().filter { it.isNotBlank() && !it.startsWith("#") }
        val bySeq = LinkedHashMap<String, MutableList<Frame>>()
        for (line in text) {
            val f = line.split("\t")
            val boxes = if (f.size > 3 && f[3].isNotBlank()) f[3].split(";").map { s ->
                s.split(",").map { it.trim().toInt() }.toIntArray()
            } else emptyList()
            bySeq.getOrPut(f[0]) { ArrayList() }
                .add(Frame(f[1].toLong(), f[2].toLong(), boxes))
        }
        return bySeq.map { (k, v) -> Seq(k, v.sortedBy { it.frameIndex }) }
    }

    private fun iou(a: IntArray, b: IntArray): Double {
        val ix = maxOf(0, minOf(a[2], b[2]) - maxOf(a[0], b[0])).toDouble()
        val iy = maxOf(0, minOf(a[3], b[3]) - maxOf(a[1], b[1])).toDouble()
        val inter = ix * iy
        val ua = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
        return if (ua <= 0) 0.0 else inter / ua
    }

    @Test
    fun realSequenceTrackingBehaviour() {
        val seqs = load()
        var tracks = 0
        var framesWithDoor = 0
        var multiFrameTracks = 0
        var fragmented = 0
        var idSwitchSuspect = 0
        val ious = ArrayList<Double>()
        val gaps = ArrayList<Long>()
        val detail = StringBuilder()

        for (s in seqs) {
            val tracker = EntranceTracker()          // 生产默认阈值：IoU 0.35 / 6 帧 / 1500ms
            val firstSeen = HashMap<String, Long>()
            val lastBox = HashMap<String, IntArray>()
            var prevFrameDoors: List<IntArray> = emptyList()
            for (f in s.frames) {
                if (f.doors.isNotEmpty()) framesWithDoor++
                // 相邻帧同一门的 IoU（用贪心一对一），用来看 0.35 这个门槛合不合理
                for (d in f.doors) {
                    val best = prevFrameDoors.map { iou(d, it) }.maxOrNull() ?: 0.0
                    if (best > 0.05) ious.add(best)
                }
                for (d in f.doors) {
                    val before = tracker.candidates().size
                    tracker.observeDoor(d[0], d[1], d[2], d[3], f.frameIndex, f.tsMs, f.tsMs)
                    val after = tracker.candidates().size
                    val id = tracker.active()?.doorId ?: continue
                    if (after > before) {
                        tracks++
                        firstSeen[id] = f.tsMs
                        // 新开的轨迹如果在空间上和某个旧轨迹高度重叠 → 疑似 ID 切换/碎片化
                        if (lastBox.entries.any { (k, v) -> k != id && iou(d, v) >= 0.35 }) {
                            idSwitchSuspect++
                        }
                    }
                    lastBox[id] = d
                }
                prevFrameDoors = f.doors
            }
            val sizes = tracker.candidates().size
            val long = tracker.candidates().count { it.seenFrames >= 2 }
            multiFrameTracks += long
            if (sizes > 0 && long == 0) fragmented++
            detail.appendLine(String.format("  %-20s 候选 %d 个，其中 ≥2 帧 %d 个", s.id, sizes, long))
        }
        println("BLV 真实序列跟踪（生产默认阈值 IoU0.35 / 6帧 / 1500ms）")
        println("  段数=${seqs.size}  有门的帧=$framesWithDoor  轨迹总数=$tracks")
        println("  ≥2 帧的稳定轨迹=$multiFrameTracks   一段都没跟稳的序列=$fragmented/${seqs.size}")
        println("  疑似 ID 切换（新轨迹与旧轨迹空间重叠≥0.35）=$idSwitchSuspect")
        if (ious.isNotEmpty()) {
            val sorted = ious.sorted()
            println(String.format("  相邻帧同一门 IoU 分布：n=%d p25=%.2f median=%.2f p75=%.2f  ≥0.35 占 %.1f%%",
                ious.size, sorted[ious.size / 4], sorted[ious.size / 2], sorted[ious.size * 3 / 4],
                100.0 * ious.count { it >= 0.35 } / ious.size))
        }
        detail.toString().lines().filter { it.isNotBlank() }.take(8).forEach { println(it) }
        // 不断言具体数字（真实视频内容决定），只要求跟踪确实发生过且可统计
        org.junit.Assert.assertTrue("轨迹数为 0 说明喂数据没喂进去", tracks > 0)
    }
}
