// Tab registry. Each tab: { title, icon, watch: [server events that make it refresh], create(ctx) -> { el, activate?, deactivate?, refresh? } }
import { stub } from './stub.js';
import { createExplorer } from './explorer.js';
import { createTagger } from './tagger.js';
import { createStage } from './stage.js';
import { createJobs } from './jobs.js';
import { createOrganizer } from './organizer.js';
import { createDates } from './dates.js';
import { createTags } from './tags.js';
import { createRules } from './rules.js';
import { createSettings } from './settings.js';

export const TABS = {
  explorer: { title: 'Explorer', icon: 'compass', watch: ['scan', 'stage', 'taxonomy'], create: createExplorer },
  tagger: { title: 'Tagger', icon: 'sparkle', watch: ['scan', 'stage', 'job'], create: createTagger },
  organizer: { title: 'Organizer', icon: 'tree', watch: ['scan', 'stage', 'taxonomy', 'job'], create: createOrganizer },
  dates: { title: 'Dates', icon: 'calendar', watch: ['scan', 'stage', 'settings'], create: createDates },
  stage: { title: 'Stage', icon: 'layers', watch: ['stage', 'scan', 'applied', 'undone'], create: createStage },
  jobs: { title: 'Jobs', icon: 'list', watch: ['job'], create: createJobs },
  tags: { title: 'Tags', icon: 'hash', watch: ['scan', 'stage'], create: createTags },
  rules: { title: 'Rules', icon: 'rules', watch: ['rules', 'stage'], create: createRules },
  settings: { title: 'Settings', icon: 'sliders', watch: ['settings'], create: createSettings },
};
