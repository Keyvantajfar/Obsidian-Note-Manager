// Tab registry. Each tab: { title, icon, watch: [server events that make it refresh], create(ctx) -> { el, activate?, deactivate?, refresh? } }
import { stub } from './stub.js';
import { createExplorer } from './explorer.js';
import { createTagger } from './tagger.js';
import { createStage } from './stage.js';
import { createJobs } from './jobs.js';

export const TABS = {
  explorer: { title: 'Explorer', icon: 'compass', watch: ['scan', 'stage', 'taxonomy'], create: createExplorer },
  tagger: { title: 'Tagger', icon: 'sparkle', watch: ['scan', 'stage', 'job'], create: createTagger },
  organizer: { title: 'Organizer', icon: 'tree', watch: ['scan', 'stage', 'taxonomy', 'job'], create: stub('Plan your folders and route notes into them.') },
  dates: { title: 'Dates', icon: 'calendar', watch: ['scan', 'stage', 'settings'], create: stub('Fix missing and wrong dates, offline.') },
  stage: { title: 'Stage', icon: 'layers', watch: ['stage', 'scan', 'applied', 'undone'], create: createStage },
  jobs: { title: 'Jobs', icon: 'list', watch: ['job'], create: createJobs },
  tags: { title: 'Tags', icon: 'hash', watch: ['scan', 'stage'], create: stub('Merge, rename and delete tag values.') },
  rules: { title: 'Rules', icon: 'rules', watch: ['rules', 'stage'], create: stub('Rules and standing instructions.') },
  settings: { title: 'Settings', icon: 'sliders', watch: ['settings'], create: stub('Model connection, automation, header schema and prompts.') },
};
