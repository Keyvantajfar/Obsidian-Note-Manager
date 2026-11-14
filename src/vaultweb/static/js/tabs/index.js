// Tab registry. Each tab: { title, icon, watch: [server events that make it refresh], create(ctx) -> { el, activate?, deactivate?, refresh? } }
import { stub } from './stub.js';

export const TABS = {
  explorer: { title: 'Explorer', icon: 'compass', watch: ['scan', 'stage', 'taxonomy'], create: stub('Browse the vault, edit headers and stage moves.') },
  tagger: { title: 'Tagger', icon: 'sparkle', watch: ['scan', 'stage', 'job'], create: stub('Review the AI’s header proposals.') },
  organizer: { title: 'Organizer', icon: 'tree', watch: ['scan', 'stage', 'taxonomy', 'job'], create: stub('Plan your folders and route notes into them.') },
  dates: { title: 'Dates', icon: 'calendar', watch: ['scan', 'stage', 'settings'], create: stub('Fix missing and wrong dates, offline.') },
  stage: { title: 'Stage', icon: 'layers', watch: ['stage', 'scan', 'applied', 'undone'], create: stub('Everything waiting to be written, with Apply and Undo.') },
  jobs: { title: 'Jobs', icon: 'list', watch: ['job'], create: stub('Watch and control AI runs.') },
  tags: { title: 'Tags', icon: 'hash', watch: ['scan', 'stage'], create: stub('Merge, rename and delete tag values.') },
  rules: { title: 'Rules', icon: 'rules', watch: ['rules', 'stage'], create: stub('Rules and standing instructions.') },
  settings: { title: 'Settings', icon: 'sliders', watch: ['settings'], create: stub('Model connection, automation, header schema and prompts.') },
};
