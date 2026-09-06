# ROADMAP

Completed items have an implementation in `bot`. Items marked **Partial** have
supporting primitives or prototypes but are not complete end-to-end workflows.

## Phase 1 - Project Setup
- [x] Create Python planner module
- [ ] Create Python bot package (**Partial:** bot module and tests exist; package metadata is pending)
- [x] Create WebSocket message schema
- [x] Implement TOML Minecraft connection configuration
- [x] Add logging
- [x] Launch local Minecraft server

## Phase 2 - WebSocket Communication
- [x] Python WebSocket server
- [ ] Rust WebSocket client
- [x] Define request/response protocol
- [x] Implement ping
- [x] Implement graceful client disconnect and server shutdown
- [x] Add request IDs
- [x] Add structured error responses
- [x] Expose essential bot primitives over WebSockets

## Phase 3 - Minecraft Connection
- [x] Connect Python bot to Minecraft
- [x] Detect successful spawn
- [x] Track player position
- [x] Track health and hunger
- [x] Track inventory
- [x] Track nearby entities
- [x] Track nearby blocks (sampled scan)

## Phase 4 - Observation API
- [x] Implement observe()
- [x] Implement get_status()
- [x] Implement block search (`find_blocks()` and nearest-block variants)
- [x] Implement inventory queries
- [x] Return summarized world state

## Phase 5 - Movement
- [x] Look at target
- [x] Walk and strafe
- [x] Jump
- [x] Stop movement
- [x] Move to coordinates
- [x] Move near a coordinate, block, or entity

## Phase 6 - Navigation
- [x] Build walkability checks
- [x] Implement bounded A* pathfinding
- [x] Execute paths
- [x] Detect stuck state
- [x] Enforce navigation timeouts
- [x] Cancel navigation and clean up failed movement
- [x] Relax A* routes when a cheaper path is discovered
- [x] Recalculate paths on request

## Phase 7 - Actions
- [x] Mine block
- [x] Collect dropped items
- [x] Equip best available tool
- [x] Place block
- [x] Send chat message
- [x] Eat food

## Phase 8 - Skills
- [x] Collect resource (navigate, equip, mine, collect, and verify inventory gain)
- [x] Craft item
- [ ] Build simple shelter

## Phase 9 - Python Agent
- [x] Connect to Ollama
- [x] Define tool schemas
- [x] Build planner loop
- [x] Execute tool calls over WebSockets
- [x] Handle failures and retries

## Phase 10 - Memory
- [ ] SQLite database
- [ ] Store observations
- [ ] Store actions
- [ ] Store results
- [ ] Store reusable skills

## Phase 11 - Evaluation
- [ ] Collect one log
- [ ] Collect five logs
- [x] Craft wooden pickaxe
- [ ] Build shelter
- [x] Pass live-server collect-resource integration test
- [ ] Run end-to-end evaluation suite

## Phase 12 - Future Features
- [ ] Combat (**Partial:** manual snowball-follow prototype)
- [ ] Farming
- [ ] Storage management
- [ ] Exploration (**Partial:** wander, follow, world scan, and block search primitives)
- [ ] Blueprint building
- [ ] Multi-agent support
- [ ] Self-improving skills
