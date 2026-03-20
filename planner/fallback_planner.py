from __future__ import annotations

from typing import Optional

from models.task import Task


class FallbackPlanner:
    def build_plan(self, goal: str, idea_text: Optional[str]) -> list[Task]:
        normalized_idea: str = (idea_text or "").lower()
        normalized_goal: str = goal.lower()

        if "stack pulse" in normalized_idea or "stack pulse" in normalized_goal:
            return self._build_stack_pulse_vertical_slice_plan()

        if "unity" in normalized_idea or "phase flip runner" in normalized_idea or "unity" in normalized_goal:
            return self._build_unity_vertical_slice_plan()

        return self._build_generic_plan(goal)

    def _build_unity_vertical_slice_plan(self) -> list[Task]:
        return [
            Task(
                id=1,
                files=["Assets/Scripts/Core/GameManager.cs"],
                instruction="Create GameManager to own run state, score tracking, fail flow, restart flow, and initialization order for the playable vertical slice.",
                type="create",
            ),
            Task(
                id=2,
                files=["Assets/Scripts/Systems/PhaseManager.cs"],
                instruction="Create PhaseManager with phase enum, current phase state, toggle API, reset API, and phase-changed event dispatch for red and blue phases.",
                type="create",
            ),
            Task(
                id=3,
                files=["Assets/Scripts/Player/PlayerController.cs"],
                instruction="Create PlayerController for forward movement, one-tap phase switching, wrong-phase fall detection, death notification, and reset-safe state handling.",
                type="create",
            ),
            Task(
                id=4,
                files=["Assets/Scripts/Systems/Platform.cs", "Assets/Scripts/Systems/Obstacle.cs"],
                instruction="Create Platform and Obstacle gameplay components for phase-bound support checks, phase-specific hazard activation, and collision-driven failure hooks.",
                type="create",
            ),
            Task(
                id=5,
                files=["Assets/Scripts/Systems/LevelChunkAnchor.cs", "Assets/Scripts/Systems/LevelSpawner.cs"],
                instruction="Create LevelChunkAnchor and LevelSpawner to support chunk layout data, safe initial chunk spawning, chunk recycling, and endless-runner progression.",
                type="create",
            ),
            Task(
                id=6,
                files=["Assets/Scripts/UI/UIManager.cs"],
                instruction="Create UIManager to show score, active phase feedback, game-over state, and restart action wiring for the playable loop.",
                type="create",
            ),
            Task(
                id=7,
                files=[
                    "Assets/Scripts/Core/GameManager.cs",
                    "Assets/Scripts/Systems/PhaseManager.cs",
                    "Assets/Scripts/Player/PlayerController.cs",
                    "Assets/Scripts/Systems/LevelSpawner.cs",
                    "Assets/Scripts/UI/UIManager.cs",
                ],
                instruction="Wire the core gameplay loop so GameManager coordinates phase changes, score updates, fail handling, restart handling, player reset, spawner reset, and UI updates.",
                type="modify",
            ),
            Task(
                id=8,
                files=["Assets/Scenes/SampleScene.unity"],
                instruction="Update the main scene wiring so the playable scene references the core managers and vertical-slice runtime objects needed for the loop.",
                type="modify",
            ),
            Task(
                id=9,
                files=["README.md"],
                instruction="Add or update README with game concept, controls, architecture overview, and project run instructions for the vertical slice.",
                type="modify",
            ),
            Task(
                id=10,
                files=["CHANGELOG.md"],
                instruction="Add an initial changelog entry for the vertical-slice gameplay systems, scene wiring, and documentation setup.",
                type="modify",
            ),
            Task(
                id=11,
                files=["AGENTS.md"],
                instruction="Update AGENTS.md with project-specific Unity extension guidance, file layout expectations, coding standards, and future agent instructions.",
                type="modify",
            ),
            Task(
                id=12,
                files=[
                    "Assets/Scripts/Core/GameManager.cs",
                    "Assets/Scripts/Systems/PhaseManager.cs",
                    "Assets/Scripts/Player/PlayerController.cs",
                    "Assets/Scripts/Systems/Platform.cs",
                    "Assets/Scripts/Systems/Obstacle.cs",
                    "Assets/Scripts/Systems/LevelChunkAnchor.cs",
                    "Assets/Scripts/Systems/LevelSpawner.cs",
                    "Assets/Scripts/UI/UIManager.cs",
                ],
                instruction="Validate the vertical-slice code paths for compile safety, explicit typing, Unity serialized field naming, low-allocation patterns, and consistent restart flow.",
                type="validate",
            ),
        ]

    def _build_generic_plan(self, goal: str) -> list[Task]:
        return [
            Task(
                id=1,
                files=["README.md"],
                instruction=f"Document the implementation plan context for this goal: {goal}",
                type="modify",
            )
        ]

    def _build_stack_pulse_vertical_slice_plan(self) -> list[Task]:
        return [
            Task(
                id=1,
                files=[
                    "Assets/Scripts/Core/GameState.cs",
                    "Assets/Scripts/Core/GameEvents.cs",
                    "Assets/Scripts/Core/GameManager.cs",
                ],
                instruction="Create the Stack Pulse core state layer with GameState enum, gameplay event definitions, and a GameManager that owns Start, Playing, Dead, score, restart flow, and startup ordering for the playable stacking loop.",
                type="create",
            ),
            Task(
                id=2,
                files=[
                    "Assets/Scripts/Systems/InputHandler.cs",
                    "Assets/Scripts/Gameplay/BlockController.cs",
                ],
                instruction="Create InputHandler and BlockController so one tap requests a drop, the active block moves horizontally before drop, and the block can switch between preview movement and falling/placement states.",
                type="create",
            ),
            Task(
                id=3,
                files=[
                    "Assets/Scripts/Systems/StackManager.cs",
                    "Assets/Scripts/Systems/ComboSystem.cs",
                    "Assets/Scripts/Systems/LevelDifficultyManager.cs",
                ],
                instruction="Create StackManager, ComboSystem, and LevelDifficultyManager to handle overlap checks, width trimming, perfect and good placement rules, fail detection, combo streaks, and progressive speed or width difficulty scaling.",
                type="create",
            ),
            Task(
                id=4,
                files=["Assets/Scripts/UI/UIManager.cs"],
                instruction="Create UIManager to show score, combo feedback, and game-over or restart prompts for the stacking vertical slice.",
                type="create",
            ),
            Task(
                id=5,
                files=[
                    "Assets/Scripts/Core/GameManager.cs",
                    "Assets/Scripts/Gameplay/BlockController.cs",
                    "Assets/Scripts/Systems/StackManager.cs",
                    "Assets/Scripts/Systems/ComboSystem.cs",
                    "Assets/Scripts/Systems/InputHandler.cs",
                    "Assets/Scripts/Systems/LevelDifficultyManager.cs",
                    "Assets/Scripts/UI/UIManager.cs",
                ],
                instruction="Wire the Stack Pulse gameplay loop so spawn, horizontal motion, one-tap drop, placement evaluation, block trimming, combo updates, score updates, miss-driven death, and restart flow all work together cleanly.",
                type="modify",
            ),
            Task(
                id=6,
                files=["Assets/Scenes/SampleScene.unity"],
                instruction="Update the main scene wiring so the playable scene references the stack gameplay managers, active block setup, camera, and runtime objects needed for the first vertical slice.",
                type="modify",
            ),
            Task(
                id=7,
                files=["README.md", "CHANGELOG.md", "AGENTS.md"],
                instruction="Add or update README, CHANGELOG, and AGENTS with Stack Pulse concept, controls, architecture overview, run instructions, coding standards, and future agent guidance.",
                type="modify",
            ),
            Task(
                id=8,
                files=[
                    "Assets/Scripts/Core/GameState.cs",
                    "Assets/Scripts/Core/GameEvents.cs",
                    "Assets/Scripts/Core/GameManager.cs",
                    "Assets/Scripts/Gameplay/BlockController.cs",
                    "Assets/Scripts/Systems/StackManager.cs",
                    "Assets/Scripts/Systems/ComboSystem.cs",
                    "Assets/Scripts/Systems/InputHandler.cs",
                    "Assets/Scripts/Systems/LevelDifficultyManager.cs",
                    "Assets/Scripts/UI/UIManager.cs",
                ],
                instruction="Validate the Stack Pulse code paths for compile safety, explicit typing, low-allocation runtime behavior, consistent one-tap flow, and clean restart behavior.",
                type="validate",
            ),
        ]
