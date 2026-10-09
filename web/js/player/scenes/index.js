// @ts-check
/**
 * Scene view factory: TimedScene.type -> view class.
 */

import { BoardSceneView } from './board.js';
import { ChapterCardView } from './card.js';
import { MediaSceneView } from './media.js';
import { QuizSceneView } from './quiz.js';
import { InteractiveSceneView } from './interactive.js';

/** @typedef {import('./types.js').SceneContext} SceneContext */
/** @typedef {import('./types.js').SceneView} SceneView */

/**
 * Which view renders a scene type (board for unknown types).
 * @param {string | null | undefined} type
 * @returns {'board' | 'card' | 'media' | 'quiz' | 'interactive'}
 */
export function viewKind(type) {
  switch (type) {
    case 'chapter_card':
      return 'card';
    case 'simulation':
    case 'ai_video':
      return 'media';
    case 'quiz_checkpoint':
      return 'quiz';
    case 'interactive':
      return 'interactive';
    default:
      return 'board';
  }
}

/**
 * @param {SceneContext} ctx
 * @returns {SceneView}
 */
export function createSceneView(ctx) {
  switch (viewKind(ctx.scene.type)) {
    case 'card':
      return new ChapterCardView(ctx);
    case 'media':
      return new MediaSceneView(ctx);
    case 'quiz':
      return new QuizSceneView(ctx);
    case 'interactive':
      return new InteractiveSceneView(ctx);
    default:
      return new BoardSceneView(ctx);
  }
}
