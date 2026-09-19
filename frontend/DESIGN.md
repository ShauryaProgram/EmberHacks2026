# Semester workspace design direction

Reading this as a student planning workspace in a quiet Modernist visual language, with ENERGY 1 / RHYTHM 2 / MOTION 1.

- Color: white and near-black establish the ground; red is reserved for urgency and current time; muted course colors identify course context.
- Layout: a narrow structural sidebar keeps destinations stable while each page uses a content width suited to its information density.
- Typography: Archivo is the sole family because its compact shapes support a Swiss editorial hierarchy without decorative type changes.
- Spacing: 56px desktop margins and 44px section gaps separate decisions; 10px row spacing keeps related task data close.
- Rules: 2px rules define major regions; 1px hairlines separate repeated information without turning rows into cards.
- Elevation: shadows appear only on overlays that sit above the page.
- Motion: transitions are limited to hover and overlay entry so the workspace stays calm during study.
- Shape: controls and panels use square corners; the task checkbox is the only circular element because completion is a distinct state.

## V4 voice direction

Reading this as: a student planning workspace for daily study, with quiet Modernist structure and one expressive interaction, dial ENERGY 1 / RHYTHM 2 / MOTION 1.

- VoiceBeam is the single expressive accent. It lives in the global voice sheet because recording is a workspace-wide action, not a task-field decoration.
- The moving color beam is tied to a real microphone stream and only appears while the transcript is actively listening.
- The transcript is the only content in the voice sheet. It does not imply task creation, search, or answer generation before those services exist.
- The square transcript stage keeps V4 compatible with the quiet Modernist shell. V5 may revise shape and surface language as one coherent system.

## V5 surface and task direction

Reading this as: a focused student workspace with soft system surfaces, restrained color, and tactile completion, dial ENERGY 2 / RHYTHM 2 / MOTION 2.

- Shape: 9px to 20px radii identify controls, major regions, and floating layers. Repeated task rows remain a flat list so the app does not become a tile grid.
- Color: the page canvas shifts to a cool neutral while work surfaces remain white. Blue is reserved for primary actions and focus, red still means urgency, and course hues still identify course context.
- Elevation: only the navigation/work regions and true overlays have shallow separation. Inner lists use spacing and hairlines.
- Task motion: Checklist motion ties the box, tick, strike, and completion together. TodoTower uses physical movement to expose the consequence of removing work from a stack. Both use the app's real tasks and respect reduced motion.
- Voice: the rounded Voice sheet is the only colorful glow. Its motion is driven by the live microphone stream and the content remains transcript-only.
- Bencho token mapping: `fill-slab` maps to the local muted surface, `fill-on` to ink, `on-ink` to the work surface, `focus` to action blue, and edge/ink RGB values to the existing theme tokens. The mapping stays scoped to the task widgets.
