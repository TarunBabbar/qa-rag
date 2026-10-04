// Visual identity per knowledge source, so a citation chip tells you at a
// glance whether the claim came from code, a ticket, a log or a requirement.
export const KIND = {
  stories: { icon: '📌', color: '#7c3aed', bg: '#f3eefe', label: 'Story' },
  'jira-testcases': { icon: '🧪', color: '#2f7d4f', bg: '#eaf6ee', label: 'Test case' },
  bugs: { icon: '🐞', color: '#c2410c', bg: '#fdeee6', label: 'Bug' },
  ui: { icon: '🖥️', color: '#2563eb', bg: '#eaf1fe', label: 'UI automation' },
  api: { icon: '🔌', color: '#0e7490', bg: '#e6f5f8', label: 'API automation' },
  framework: { icon: '🧱', color: '#4338ca', bg: '#eeeffd', label: 'Framework' },
  testcases: { icon: '🧪', color: '#2f7d4f', bg: '#eaf6ee', label: 'Test case' },
  jira: { icon: '🐞', color: '#2563eb', bg: '#eaf1fe', label: 'Jira' },
  docs: { icon: '📄', color: '#b45309', bg: '#fdf3e4', label: 'Doc' },
  transcript: { icon: '🗓️', color: '#7c3aed', bg: '#f3eefe', label: 'Meeting' },
  diagram: { icon: '🔷', color: '#0e7490', bg: '#e6f5f8', label: 'Diagram' },
  logs: { icon: '🧯', color: '#c2410c', bg: '#fdeee6', label: 'CI log' },
  code: { icon: '⌨️', color: '#4338ca', bg: '#eeeffd', label: 'Code' },
  figma: { icon: '🎨', color: '#be185d', bg: '#fdecf4', label: 'Figma' },
};

export const kindOf = (k) => KIND[k] || { icon: '•', color: '#6f6b60', bg: '#f1efe8', label: k || 'source' };
