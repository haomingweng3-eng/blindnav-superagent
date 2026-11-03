package com.blindnav.app.scene

/**
 * 播报策略（4+1 的 SpeechPolicy）。
 *
 * 三种来源共用一个出口，优先级严格分开：
 *   URGENT   本地安全链：有人横穿、通道被占、请停下重观察。**永远不等云端**。
 *   AMBIENT  低频环境描述：只在"有新信息"且过了节流时才播。
 *   ANSWER   用户主动问"前面有什么"：这是对提问的回应，不该被环境节流挡掉。
 *
 * 三条设计选择，都是为了让它"不烦人"：
 *  1. AMBIENT 没有新信息就返回 SILENT。沉默是正常状态，不是失败。
 *  2. 按类别节流：同一个人/自行车这类短时间内反复出现变化不重复播；
 *     但**新类别**（第一次出现台阶）不受旧类别节流影响，否则会把关键信息压掉。
 *  3. URGENT 可以打断 AMBIENT，但 AMBIENT 永远不能打断 URGENT，
 *     也不得伪装成安全提示（措辞里不许出现"可以通过/安全/放心"）。
 *
 * 这一层不产生任何移动指令。措辞由 [SceneNarrator] 生成，且受同一套禁用词约束。
 */
enum class SpeechPriority { URGENT, AMBIENT, ANSWER }

enum class SpeechAction { SPEAK, SILENT, DEFER }

data class SpeechRequest(
    val priority: SpeechPriority,
    val text: String,
    val categories: Set<String> = emptySet(),
    val nowMs: Long,
    /** 只有 AMBIENT 需要这个；URGENT/ANSWER 不受 delta 约束 */
    val hasNews: Boolean = true
)

data class SpeechDecision(val action: SpeechAction, val reason: String, val text: String = "")

class SpeechPolicy(
    /** 同类环境播报的最小间隔 */
    private val ambientCategoryCooldownMs: Long = 12_000L,
    /** 任意环境播报的最小间隔（防止连续多条） */
    private val ambientGlobalCooldownMs: Long = 8_000L,
    /** 安全播报的最短间隔：同一句危险提示不要每帧重复 */
    private val urgentRepeatMs: Long = 3_000L,
    /** DEFER 的最长等待：超过它就放弃这条，不无限期压着 */
    private val maxDeferMs: Long = 4_000L
) {
    // 不能用 Long.MIN_VALUE 当"从未播过"的哨兵：`nowMs - Long.MIN_VALUE` 会溢出成负数，
    // 于是冷却判断永远成立，第一条环境播报被永久压死（真实帧回放里 dedup=0 就是这么来的）。
    private var lastAmbientAt = NOT_SET
    private val lastAmbientByCategory = HashMap<String, Long>()
    private var lastUrgentText = ""
    private var lastUrgentAt = NOT_SET
    private var deferred: SpeechRequest? = null

    private val FORBIDDEN = Regex(
        "可以(直接)?(走|过|进)|往[左右]边?走|向[左右]边?挪|[0-9]+\\s*[米步]|放心|安全通行|安不安全|可以通过")

    fun decide(req: SpeechRequest): SpeechDecision {
        if (req.text.isBlank()) return SpeechDecision(SpeechAction.SILENT, "empty")
        // 越权措辞在出口再拦一次：即使上游模型没拦住，也不会播出去
        if (FORBIDDEN.containsMatchIn(req.text)) {
            return SpeechDecision(SpeechAction.SILENT, "forbidden_phrasing:${req.text.take(24)}")
        }
        return when (req.priority) {
            SpeechPriority.URGENT -> {
                if (req.text == lastUrgentText && lastUrgentAt != NOT_SET &&
                    req.nowMs - lastUrgentAt < urgentRepeatMs
                ) {
                    SpeechDecision(SpeechAction.SILENT, "urgent_repeat")
                } else {
                    lastUrgentText = req.text; lastUrgentAt = req.nowMs
                    SpeechDecision(SpeechAction.SPEAK, "urgent", req.text)
                }
            }

            SpeechPriority.ANSWER -> {
                // 回答用户提问：可以挤掉还没播出去的 AMBIENT，但不挤掉 URGENT
                deferred = null
                SpeechDecision(SpeechAction.SPEAK, "answer", req.text)
            }

            SpeechPriority.AMBIENT -> {
                if (!req.hasNews) return SpeechDecision(SpeechAction.SILENT, "no_news")
                if (lastAmbientAt != NOT_SET && req.nowMs - lastAmbientAt < ambientGlobalCooldownMs) {
                    return holdOrDrop(req, "ambient_global_cooldown")
                }
                val fresh = req.categories.filter {
                    val at = lastAmbientByCategory[it] ?: NOT_SET
                    at == NOT_SET || req.nowMs - at >= ambientCategoryCooldownMs
                }
                if (req.categories.isNotEmpty() && fresh.isEmpty()) {
                    return holdOrDrop(req, "ambient_category_cooldown")
                }
                lastAmbientAt = req.nowMs
                for (c in req.categories) lastAmbientByCategory[c] = req.nowMs
                SpeechDecision(SpeechAction.SPEAK, "ambient", req.text)
            }
        }
    }

    private fun holdOrDrop(req: SpeechRequest, why: String): SpeechDecision {
        val prev = deferred
        if (prev != null && req.nowMs - prev.nowMs > maxDeferMs) {
            deferred = req
            return SpeechDecision(SpeechAction.SILENT, "dropped_stale_deferred")
        }
        if (prev == null) deferred = req
        return SpeechDecision(SpeechAction.DEFER, why)
    }

    /** 上一轮 DEFER 的请求，等冷却过去后由调用方重新 decide。 */
    fun pending(): SpeechRequest? = deferred

    fun clearPending() { deferred = null }

    fun reset() {
        lastAmbientAt = NOT_SET; lastAmbientByCategory.clear()
        lastUrgentText = ""; lastUrgentAt = NOT_SET; deferred = null
    }

    companion object {
        /** 安全的"从未发生"哨兵：够小又不参与减法溢出。 */
        private const val NOT_SET = -1L
    }
}

/**
 * 把 SceneDelta 翻成一句话。规则很简单：
 * 只说差分里的事，最多两小句，不描述没变的东西。
 */
object SceneNarrator {

    private val NAME = mapOf(
        "person" to "行人", "bicycle" to "自行车", "car" to "车", "motorcycle" to "摩托车",
        "doorway" to "入口", "door" to "门", "shopfront" to "店面", "stairs" to "台阶",
        "curb" to "路缘", "obstacle" to "障碍", "pole" to "立柱", "ramp" to "坡道",
        "crosswalk" to "斑马线", "bin" to "垃圾桶", "bench" to "长椅", "sign" to "招牌")

    private val STATE = mapOf(
        SceneState.MOVING to "在移动", SceneState.CROSSING to "正在横穿",
        SceneState.OPENING to "正在打开", SceneState.CLOSED to "关着",
        SceneState.STATIC to "", SceneState.UNKNOWN to "")

    fun sideLabel(s: SceneSide): String = when (s) {
        SceneSide.FRONT -> "正前方"; SceneSide.LEFT -> "左边"; SceneSide.RIGHT -> "右边"
        SceneSide.UP -> "上方"; SceneSide.DOWN -> "脚下"; SceneSide.UNKNOWN -> "附近"
    }

    fun phrase(o: SceneObject): String {
        val n = NAME[o.category] ?: o.category
        val st = STATE[o.state] ?: ""
        return if (st.isEmpty()) n else "$n$st"
    }

    /** 环境播报：只讲新出现的与状态变化的。返回空串表示无话可说。 */
    fun narrate(d: SceneDelta, maxItems: Int = 3): String {
        val parts = ArrayList<String>()
        for (o in d.appeared.take(maxItems)) parts.add("${sideLabel(o.side)}有${phrase(o)}")
        for ((_, now) in d.stateChanged.take(maxItems - parts.size)) {
            parts.add("${sideLabel(now.side)}的${phrase(now)}")
        }
        if (parts.isEmpty() && d.newTexts.isNotEmpty()) {
            parts.add("读到文字「${d.newTexts.first().take(12)}」")
        }
        return parts.take(2).joinToString("，")
    }
}
