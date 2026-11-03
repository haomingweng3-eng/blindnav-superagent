package com.blindnav.app.last10m

/**
 * OCR 帧闸门（任务书第 8 节 / PF-10）。纯 Kotlin，时钟与事件都由调用方注入，
 * 所以"识别卡死不恢复"这类问题能在电脑上写出确定性测试。
 *
 * 它管三件事：
 *  1. **节流**：两次推理最短间隔。被节流挡掉的帧单独计数（`throttled`），
 *     不混进 `dropped`——上一版只统计了"忙不过来丢的帧"，面板显示的丢帧率是低的。
 *  2. **不排队**：上一帧还在跑，这一帧直接丢。排队等于让系统一直读几秒前的画面，
 *     而门口最要命的恰恰是证据新鲜度。
 *  3. **看门狗**：一帧跑超过 [timeoutMs] 就判定超时，释放闸门让下一帧进去。
 *     迟到结果用世代号（generation）作废：`finish()` 只接受当前世代。
 *
 * 第 3 条为什么不能只靠 `Tasks.await(task, timeout)`：ML Kit 的 await 超时只让**等待者**
 * 脱身，工作线程还挂在里面；没有世代号的话，那个线程后来真的返回时，
 * 会把几秒前那一帧的文字当成刚看到的喂进状态机——这就是证据污染。
 */
class OcrGate(
    private val minIntervalMs: Long = 300L,
    private val timeoutMs: Long = DEFAULT_TIMEOUT_MS,
    private val clock: () -> Long,
    private val events: (String) -> Unit = {}
) {

    companion object {
        /** 工程看门狗，不是算法延迟指标。桌面 benchmark 的 latency 不能拿来当这个数。 */
        const val DEFAULT_TIMEOUT_MS = 3_000L
        private const val NOT_SET = -1L
    }

    enum class Accept { ACCEPTED, THROTTLED, BUSY_DROPPED }

    var runs = 0L; private set
    var throttled = 0L; private set
    var dropped = 0L; private set
    var timeouts = 0L; private set
    var lateResultsIgnored = 0L; private set

    private var lastRunAt = NOT_SET
    private var generation = 0L
    private var busyGeneration = NOT_SET
    private var busySince = NOT_SET
    private var busyFrameId = NOT_SET

    /** 当前还在跑的那一帧（没有就 -1）。面板要能显示"OCR 正在忙哪一帧"。 */
    fun inFlightFrame(): Long = if (busyGeneration == NOT_SET) -1L else busyFrameId
    fun busy(): Boolean = busyGeneration != NOT_SET

    /**
     * 申请跑一帧。返回世代号表示同意；null 表示这一帧不进（被节流或丢弃）。
     * 同意之后**必须**调用 [finish]，否则看门狗会在 [timeoutMs] 后强行放开闸门。
     */
    fun tryBegin(frameId: Long): Long? {
        val now = clock()
        watchdogIfOverdue(now)
        if (busyGeneration != NOT_SET) {
            dropped++
            events("OCR_BUSY_DROP frame=$frameId inFlight=$busyFrameId")
            return null
        }
        if (lastRunAt != NOT_SET && now < lastRunAt) {
            // 单调时钟不该倒退，但这里不依赖那个假设：倒退时把基线搬到现在，
            // 否则 `now - lastRunAt` 是负数，节流判断会永远成立，OCR 被永久冻住。
            events("CLOCK_REWIND last=$lastRunAt now=$now —— 重置节流基线，不停摆")
            lastRunAt = NOT_SET
        }
        if (lastRunAt != NOT_SET && now - lastRunAt < minIntervalMs) {
            throttled++
            return null
        }
        lastRunAt = now
        generation++
        busyGeneration = generation
        busySince = now
        busyFrameId = frameId
        return generation
    }

    /**
     * 一帧跑完（或跑失败）。只有世代号匹配才算数——迟到结果一律作废并计数。
     * @return true 表示这次结果可以交付给状态机
     */
    fun finish(generation: Long, frameId: Long, latencyUs: Long, ok: Boolean): Boolean {
        val now = clock()
        if (generation != busyGeneration || busyGeneration == NOT_SET) {
            lateResultsIgnored++
            events("OCR_LATE_RESULT_IGNORED frame=$frameId gen=$generation " +
                "current=${if (busyGeneration == NOT_SET) "-" else busyGeneration.toString()} now=$now")
            return false
        }
        val elapsed = now - busySince
        busyGeneration = NOT_SET
        busySince = NOT_SET
        busyFrameId = NOT_SET
        if (!ok) {
            events("OCR_FAILED frame=$frameId us=$latencyUs elapsed=${elapsed}ms")
            return true
        }
        runs++
        events("OCR_RESULT frame=$frameId us=$latencyUs elapsed=${elapsed}ms")
        return true
    }

    /** 看门狗：超过 [timeoutMs] 还没 finish，就当作这一次识别死掉了。 */
    private fun watchdogIfOverdue(now: Long) {
        if (busyGeneration == NOT_SET) return
        val elapsed = now - busySince
        if (elapsed <= timeoutMs) return
        timeouts++
        events("OCR_TIMEOUT frame=$busyFrameId elapsed=${elapsed}ms limit=${timeoutMs}ms")
        busyGeneration = NOT_SET
        busySince = NOT_SET
        busyFrameId = NOT_SET
    }

    fun stats(): String = "ocr runs=$runs dropped=$dropped throttled=$throttled " +
        "timeouts=$timeouts late=$lateResultsIgnored busy=${busy()} inFlight=${inFlightFrame()}"

    /** 给真机用的重置：切换目标或重开一帧流时清空节流基线。 */
    fun reset() {
        lastRunAt = NOT_SET
        busyGeneration = NOT_SET
        busySince = NOT_SET
        busyFrameId = NOT_SET
    }
}
