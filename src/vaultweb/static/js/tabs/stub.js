// Placeholder panel for tabs that are built later in the roadmap.
import { h } from '../core.js';
import { emptyState } from '../ui.js';
export const stub = (blurb) => (ctx) => ({ el: h('div', { class: 'view' }, emptyState({ title: 'Coming in the next step', text: blurb })) });
