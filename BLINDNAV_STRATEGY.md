# BlindNav Strategy

## Product Goal

BlindNav is a phone-based assistance system for blind and low-vision users. It
works with the white cane, not instead of it. The cane remains the primary
near-field physical safeguard.

Core capabilities:

1. **Walking Corridor** — detect whether the user is leaving the walkable area.
2. **Obstacle Risk** — detect obstacles and dynamic risks several metres ahead
   that occupy the walking corridor.
3. **Last10m** — after map arrival: SEARCH → LOCK → ENTRANCE → ARRIVAL.
4. **Scene / Text Awareness** — keep watching and remembering, but speak only
   for danger, task-critical change, or an explicit user question.
5. **Voice Agent** — later: ASR → BlindNav Agent → Tools → TTS. Voice never
   changes Safety Authority.

## Technical Principles

### Walking

General Traversability → Walkable Corridor. IMU / visual motion must distinguish
user drift from phone rotation. A phone-only turn must not become a LEFT/RIGHT
command.

### Obstacle

`near == STOP` is forbidden. STOP requires corridor occupancy, distance/near
evidence, temporal persistence, approaching/crossing, walking state, and fresh
evidence. An unknown object that persistently occupies the corridor may STOP.
The white cane remains responsible for immediate near-ground contact.

### Last10m

Door ≠ Arrival. Seeing a sign ≠ Arrival. Target evidence, door evidence,
relation and ground must be fresh and from the same session. UNKNOWN is valid.
Prefer REOBSERVE over guessing the wrong entrance.

### LLM / VLM

LLM/VLM may describe, explain, recognise and propose candidates. They may not
authorise GO / LEFT / RIGHT / MOVE_FORWARD. Motion safety is decided by
deterministic Risk / Motion Authority.

## Priority

- P0 General Traversability — corridor must exist without tactile paving.
- P1 Real-video Obstacle Evaluation — replace synthetic-only evidence.
- P2 Scene / Text Awareness — real data, BLV usefulness, not caption quality.
- P3 Last10m Automatic Entrance Proposal — correct door must enter candidates.
- P4 Target–Door Relation — real relation GT.
- P5 Voice Agent — not the current blocker.

## Safety Boundary

BlindNav is not a certified mobility aid. It must never claim production safety,
replace the cane, or authorise movement from a model's free text.
