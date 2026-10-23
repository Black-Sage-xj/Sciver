"""External labels use explicit sources, nearest timestamps, then predecessors."""
from __future__ import annotations
import shutil
import subprocess
import unittest
from test_research_explorer_g6 import HARNESS, SCRIPT

ADDITIONAL = r"""
  // Explicit sources win; other records use nearest time or predecessor.
  nodes.push(
    {id:'tm-v2-cycle-cycle-0055',label:'C55 source',semantic:{type:'External'},
      text:'type: External\nid: tm-v2-cycle-cycle-0055\ntimestamp: 09-26-23:53\n'},
    {id:'tm-v2-cycle-cycle-0087',label:'C87 source',semantic:{type:'External'},
      text:'type: External\nid: tm-v2-cycle-cycle-0087\ntimestamp: 09-28-13:30\n'},
    {id:'external-dated',label:'Archive event',semantic:{type:'External'},
      text:'type: External\ntimestamp: 09-27-11:00\n# Archive event'},
    {id:'external-old',label:'Legacy event',semantic:{type:'External'},
      text:'type: External\ntimestamp: 09-08-19:21\n# Legacy event'},
    {id:'external-source90',label:'Source 90',semantic:{type:'External'},
      text:'type: External\nsource_cycle: 90\n# Source 90'},
    {id:'external-no-time',label:'No recorded time',semantic:{type:'External'},
      text:'type: External\n# No timestamp'},
    {id:'external-follow',label:'No time with predecessor',semantic:{type:'External'},
      text:'type: External\n# Follow-up'},
    {id:'external-c86',label:'Source C86',semantic:{type:'External'},
      text:'type: External\nsource_cycle: 86\ntimestamp: 2026-10-13\n# Source C86'},
    {id:'external-c97',label:'Source C97',semantic:{type:'External'},
      text:'type: External\nsource_cycle: 97\ntimestamp: 10-14-18:34\n# Source C97'},
    {id:'external-later',label:'Later event',semantic:{type:'External'},
      text:'type: External\ntimestamp: 10-16-08:32\n# Later event'},
    {id:'historical-c88-decision',label:'C88 decision source',semantic:{type:'External'},
      text:'type: External\n# Historical decision'},
    {id:'audit-finding-c69-c74',label:'Two Cycle sources',semantic:{type:'External'},
      text:'type: External\n# Ambiguous source'});
  edges.push({id:'D-follow',fromNode:'D',toNode:'external-follow'},
             {id:'B-follow',fromNode:'B',toNode:'external-follow'});
  await elements['ex-refresh'].onclick();await tick();await tick();
  await elements['ex-all'].onclick();await tick();await tick();
  latestGraph.camera.zoom=.34;
  latestGraph.events.aftertransform();
  const extLabel=id=>{
    const node=latestGraph.getNodeData(id);
    assert(node,'test External remains in G6 data: '+id);
    return node.data.compactLabel;
  };
  assert.equal(extLabel('tm-v2-cycle-cycle-0055'),'E55');
  assert.equal(extLabel('tm-v2-cycle-cycle-0087'),'E87');
  assert.equal(extLabel('external-dated'),'E55',
    'dated External chooses the nearest trusted timestamp anchor');
  assert.equal(extLabel('external-old'),'E55',
    'before the earliest anchor uses that anchor');
  assert.equal(extLabel('external-source90'),'E90',
    'missing date but explicit provenance source_cycle retains reference number');
  assert.equal(extLabel('external-no-time'),'E?',
    'without time, source or predecessor, show unknown');
  assert.equal(extLabel('external-follow'),'E150',
    'without time, use the latest Cycle among nearest predecessors');
  assert.equal(extLabel('external-later'),'E97',
    'after the latest anchor uses that anchor');
  assert.equal(extLabel('historical-c88-decision'),'E88');
  assert.equal(extLabel('audit-finding-c69-c74'),'E?',
    'multiple Cycle tokens in one ID are ambiguous');
  for(const id of ['tm-v2-cycle-cycle-0055','tm-v2-cycle-cycle-0087',
      'external-source90','historical-c88-decision','external-dated','external-follow'])
    assert.match(extLabel(id),/^E\d+$/,'External labels use one numeric value: '+id);
  assert.equal(latestGraph.getNodeData('external-dated').data.semantic.cycle,undefined,
    'External provenance does not become scientific Cycle authority');
  assert.equal(elements['ex-source-rail'].hidden,true,
    'source index is absent in compact overview');
  latestGraph.events['node:click']({target:{id:'external-dated'}});await tick();await tick();
  assert(elements['ex-reader'].innerHTML.includes('按最近的明确 Cycle 时间锚点估算'),
    'clicking External explains timestamp-based placement');
  latestGraph.camera.zoom=.60;latestGraph.events.aftertransform();
  assert.equal(elements['ex-compact-labels'].hidden,false);
  assert.equal(elements['ex-source-rail'].hidden,true);
  latestGraph.camera.zoom=.601;latestGraph.events.aftertransform();
  assert.equal(elements['ex-compact-labels'].hidden,true);
  assert.equal(elements['ex-source-rail'].hidden,false);
"""
class ExternalTimeTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js required')
    def test_external_temporal_labels(self):
        needle="  console.log('G6 view model test:"
        self.assertIn(needle,HARNESS)
        code=HARNESS.replace(needle,ADDITIONAL+'\n'+needle)
        proc=subprocess.run(['node','-e',code,str(SCRIPT)],capture_output=True,text=True,timeout=25)
        self.assertEqual(proc.returncode,0,proc.stderr)
