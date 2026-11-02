package com.blindnav.app

/** Chooses the most useful ground hazard when several boxes share a frame. */
object GroundHazardPolicy {
    data class Candidate(val name: String, val side: String, val near: Boolean, val score: Float)

    private fun classPriority(name: String): Int = when (name) {
        "台阶" -> 4
        "树", "垃圾桶" -> 3
        "斑马线" -> 2
        else -> 1
    }

    fun choose(candidates: List<Candidate>): Candidate? = candidates.maxWithOrNull(
        compareBy<Candidate> { if (it.near) 2 else 1 }
            .thenBy { classPriority(it.name) }
            .thenBy { it.score }
    )
}
