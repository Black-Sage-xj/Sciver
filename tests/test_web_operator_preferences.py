"""No production task is changed: SDK preferences and model wiring fixture tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from theory_master.operator_preferences import read, write
from theory_master.operator_control import dispatch


class WebOperatorPreferencesTests(unittest.TestCase):
    def test_persist_task_scoped_flags_models_and_invalid_reject(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            self.assertEqual(read(root), {'flags':{},'models':{}})
            write(root,kind='flag',key='error_repair',value=False)
            write(root,kind='flag',key='cycle_audit',value=True)
            write(root,kind='model',key='attention_review',value='gpt-6-sol')
            write(root,kind='model',key='cycle_audit',value='gpt-6.1-sol')
            data=read(root)
            self.assertEqual(data['flags'],{'error_repair':False,'cycle_audit':True})
            self.assertEqual(data['models'],{'attention_review':'gpt-6-sol','cycle_audit':'gpt-6.1-sol'})
            write(root,kind='model',key='cycle_audit',value='')
            self.assertNotIn('cycle_audit',read(root)['models'])
            write(root,kind='model',key='cycle_audit',value='gpt-6.1-sol')
            for entry in ({'kind':'flag','key':'quota_auto_adjust','value':True},
                          {'kind':'flag','key':'cycle_audit','value':'false'},
                          {'kind':'model','key':'error_repair','value':'gpt-6-sol'},
                          {'kind':'model','key':'attention_review','value':'unknown'}):
                with self.assertRaises(ValueError):
                    write(root,**entry)
            other=root/'another-task'
            (other/'.theory-master').mkdir(parents=True)
            self.assertEqual(read(other),{'flags':{},'models':{}})

    def test_dispatch_uses_saved_flags_for_managed_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            write(root,kind='flag',key='cycle_audit',value=True)
            write(root,kind='flag',key='error_repair',value=False)
            conf=root/'theory-master.config.json'
            conf.write_text('{}')
            fake=SimpleNamespace(master_id=1,research_root=root)
            request={'task_root':str(root),'research_root':str(root),'run_id':1,
                     'action':'resume','value':{}}
            with patch('theory_master.operator_control.TaskBinding.load',return_value=SimpleNamespace(config_path=conf)):
                with patch('theory_master.operator_control.TaskSpec.from_config',return_value=fake):
                    with patch('theory_master.runtime_manager.runtime_health',return_value={'active':False}):
                        with patch('theory_master.runtime_manager.launch_managed_runtime',return_value={'status':'started','launch_id':'mock'}) as launcher:
                            result=dispatch(request)
                            self.assertEqual(result['launch_status'],'started')
                            self.assertTrue(launcher.call_args.kwargs['cycle_audit'])
                            # The merged Supervisor switch is hot-read; the legacy
                            # OFF flag still keeps it closed.
                            self.assertIsNone(launcher.call_args.kwargs['error_repair'])
                            from theory_master.supervisor_intervention import supervisor_enabled
                            self.assertFalse(supervisor_enabled(root))

    def test_audit_relay_respects_model_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            (root/'.theory-master').mkdir()
            cycle=root/'1'
            cycle.mkdir()
            write(root,kind='model',key='cycle_audit',value='gpt-6-sol')
            from theory_master.observability.cycle_audit_relay import spawn_cycle_audit_agent
            import subprocess
            skill = root / 'audit-skill.md'
            skill.write_text('# Offline audit fixture\n', encoding='utf-8')
            with (patch('theory_master.observability.cycle_audit_relay.subprocess.run',
                        return_value=SimpleNamespace(returncode=0,stdout='ok',stderr='')) as cli,
                  patch('theory_master.observability.cycle_audit_relay.AUDIT_SKILL', skill)):
                result=spawn_cycle_audit_agent({'run_dir':str(cycle),'cycle_id':'1'})
                self.assertEqual(result,'ok')
                command=cli.call_args.args[0]
                self.assertEqual(command[command.index('--model')+1],'gpt-6-sol')
