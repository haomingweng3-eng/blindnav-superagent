package com.blindnav.app.last10m

/**
 * 最后十米的事件名清单（任务书第 6 节 / PF-08）。
 *
 * 为什么单独列一张表：上一版整个链路在 logcat 里只有三行
 * （SESSION_START / OCR / SESSION_CLOSE），现场一旦"点了没反应"，
 * 事后完全无法复盘。这张表同时是：
 *  - 日志格式的唯一出处（grep 得到名字，不会写着写着改名）；
 *  - 事件覆盖测试的断言对象（每个名字都必须真的有人发过）；
 *  - 现场排错手册（[why] 说明这条事件回答什么问题）。
 *
 * 每行事件统一长这样，方便 `grep` 与后续脚本解析：
 *   `EV <NAME> session=<s> frame=<f> ts=<ms> state=<..> decision=<..> <细节>`
 */
object Last10mEvents {

    val NAMES = listOf(
        "SESSION_START", "SESSION_END",
        "FRAME_ACCEPTED", "FRAME_DROPPED",
        "OCR_START", "OCR_RESULT", "OCR_TIMEOUT", "OCR_FAILED", "OCR_BUSY_DROP", "OCR_LATE_RESULT_IGNORED",
        "TARGET_SEEN", "TARGET_LOCKED", "TARGET_EXPIRED",
        "DOOR_ADDED", "DOOR_UPDATED", "DOOR_EXPIRED", "DOOR_INVALIDATED", "MULTIPLE_DOORS",
        "GROUND_EVIDENCE",
        "RELATION_CONFIRMED", "RELATION_CONFLICT", "RELATION_INVALIDATED",
        "STATE_TRANSITION", "DECISION", "NEED_VIEW",
        "ROUTE_BEARING", "BLOCKING_CONFLICT", "ADDRESS_EXPECT", "ADDRESS_CONFLICT", "VLM_VIOLATION",
        "SPEECH_ENQUEUED", "SPEECH_SUPPRESSED",
        "CAPTURE_GROUP_START", "CAPTURE_SAVED", "CAPTURE_FAILED", "CAPTURE_REJECTED", "CAPTURE_GROUP_END",
        "RELATION_MARKED"
    )

    /** 现场排错手册：这条事件回答什么问题。 */
    fun why(name: String): String = when (name) {
        "FRAME_ACCEPTED" -> "相机确实把帧交给了最后十米（没交就是 CameraX 侧问题）"
        "OCR_RESULT" -> "一帧的识别耗时与命中数；Android 延迟只能从这里读，不能拿桌面数字代替"
        "OCR_TIMEOUT" -> "看门狗放开闸门：识别卡死过，设备侧需要查 ML Kit"
        "OCR_LATE_RESULT_IGNORED" -> "迟到的识别结果被作废，没有污染状态机"
        "TARGET_LOCKED" -> "凭什么说认出了目标（alias + 档位 + 帧数）"
        "TARGET_EXPIRED" -> "身份锁失效，同时关系作废——防'旧目标 + 新门 = 假确认'"
        "DOOR_ADDED" -> "候选门来自模型还是人工（src=DEBUG_MANUAL_PROPOSAL 就不是检测结果）"
        "MULTIPLE_DOORS" -> "邻门场景真实出现过，冲突路径有被走到"
        "GROUND_EVIDENCE" -> "门前地面证据是谁给的：人工声明不等于视觉确认安全"
        "RELATION_CONFIRMED" -> "招牌与门被判属同一店面；生产尚未接入，出现这条就必须有 VLM 事实来源"
        "NEED_VIEW" -> "系统下一步要什么视角，以及为什么"
        "SPEECH_SUPPRESSED" -> "这句话为什么没说（去重/冷却/STOP 优先/禁用措辞）"
        "CAPTURE_REJECTED" -> "这张图为什么没进组：会话身份不符，防 A 店招牌配 B 店的门"
        "CAPTURE_GROUP_END" -> "这组缺哪些 kind，写进 manifest 的 missing_kinds"
        else -> ""
    }
}
