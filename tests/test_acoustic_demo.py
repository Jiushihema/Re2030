"""声波演示的独立数学检查、闭环与断路反例。全部不依赖真实设备。"""
import cmath
import copy
import hashlib
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from acoustic_demo.application import AcousticApplication, AcousticEngine, AcousticStation, SCENARIO
from acoustic_demo.model import AcousticResponse, DemoModel, DATA
from acoustic_demo.server import AcousticServer
from sim2030.records import RunReader
from sim2030.presentation.server import PresentationServer
from sim2030.scenario import load_scenario


class TestAcousticMath(unittest.TestCase):
    def test_reference_integrity_and_normal_axis_scales(self):
        model = DemoModel()
        self.assertEqual(len(model.baseline), 16000)
        self.assertEqual(hashlib.sha256((DATA / "normal_xyz.csv.gz").read_bytes()).hexdigest(),
                         model.provenance["packaged_sha256"])
        for actual, expected in zip(model.scales, (590.2318785421852, 1469.4844148498241, 565.34793127288)):
            self.assertAlmostEqual(actual, expected, places=8)
        model.sample_to(1.01)
        for row in model.last_wave:
            self.assertEqual(row[2:5], row[8:11])
            self.assertEqual(row[5:8], [0, 0, 0])

    def test_continuous_response_matches_frequency_domain(self):
        response = AcousticResponse()
        response.switch(.3, 0)
        w0, wc, w = 2 * math.pi * 5500, 2 * math.pi * 1500, 2 * math.pi * 5500
        transfer = w0*w0 / ((1j*w)**2 + w0/17*1j*w + w0*w0) * wc / (1j*w+wc)
        for t in (.20, .20025, .201, .2025):
            expected = (.3 * transfer * cmath.exp(1j*w*t)).real
            self.assertAlmostEqual(response.value(t)[1], expected, places=8)

    def test_analytic_transients_agree_with_independent_rk4(self):
        # 独立地对三个实数微分方程做 RK4，不复用解析解的极点/余数。
        response = AcousticResponse()
        response.switch(.3, 0)
        response.switch(0, .03)
        w0, wc = 2 * math.pi * 5500, 2 * math.pi * 1500
        def derivative(t, state):
            envelope = (.5-.5*math.cos(math.pi*t/.02)) if t < .02 else 1
            if t >= .03:
                envelope = (.5+.5*math.cos(math.pi*(t-.03)/.02)) if t < .05 else 0
            u = .3 * envelope * math.cos(w0*t)
            x, velocity, output = state
            return velocity, w0*w0*(u-x)-w0/17*velocity, wc*(x-output)
        state, dt, errors, references = (0, 0, 0), 1/256000, [], []
        for index in range(round(.065 / dt)):
            t=index*dt
            a=derivative(t,state)
            b=derivative(t+dt/2,tuple(s+dt*v/2 for s,v in zip(state,a)))
            c=derivative(t+dt/2,tuple(s+dt*v/2 for s,v in zip(state,b)))
            d=derivative(t+dt,tuple(s+dt*v for s,v in zip(state,c)))
            state=tuple(s+dt*(a0+2*b0+2*c0+d0)/6 for s,a0,b0,c0,d0 in zip(state,a,b,c,d))
            if (index+1)%64==0:
                exact=response.value((index+1)*dt)[1]
                errors.append((state[2]-exact)**2)
                references.append(exact**2)
        self.assertLess(math.sqrt(sum(errors)/sum(references)), .002)

    def test_xyz_are_additive_and_original_amplitude_is_preserved(self):
        model=DemoModel()
        model.response.switch(.3, 1)
        features=model.sample_to(3)
        # 旧 TI01_S CSV 在 [1,2) 秒的三轴统计；解析解相对旧数值积分略有差异。
        for actual, expected in zip(features[1]["rms_ratio_xyz"],
                                    (1.3757690829, 1.1387741312, 1.0170288140)):
            self.assertAlmostEqual(actual, expected, delta=.002)
        for row in model.last_wave:
            for i in range(3):
                self.assertAlmostEqual(row[8+i],row[2+i]+row[5+i],places=9)
            # 三轴共享参考响应，各除以自己的基准尺度后，比例严格为权重。
            normalized=[row[5+i]/model.scales[i] for i in range(3)]
            self.assertAlmostEqual(normalized[1],.6*normalized[0],places=10)
            self.assertAlmostEqual(normalized[2],.3*normalized[0],places=10)

    def test_thermal_balance_and_inertial_coast(self):
        model=DemoModel()
        model.advance(20)
        self.assertAlmostEqual(model.oil_c,55,places=9)
        model.set_power(False,20)
        model.advance(21)
        self.assertAlmostEqual(model.current_a,2*math.exp(-4),places=9)
        self.assertAlmostEqual(model.speed_ratio,math.exp(-.2),places=9)
        self.assertGreater(model.oil_c,55)
        self.assertLess(model.oil_sensor_c,model.oil_c)
        model.advance(1820)
        self.assertGreater(model.oil_c,60)
        self.assertLess(model.oil_c,85)  # 停机后仍有自然散热，平衡温度 85 ℃。

    def test_accelerated_heat_has_20s_tau_and_unscaled_comparison(self):
        fast=DemoModel({'time_scale':360})
        slow=DemoModel()
        fast.set_power(False,0)
        slow.set_power(False,0)
        fast.advance(12.5)
        slow.advance(12.5)
        self.assertEqual(fast.natural_tau_s,20)
        self.assertEqual(fast.reference_natural_tau_s,7200)
        self.assertEqual(fast.effective_capacity_j_per_k,10000)
        self.assertGreater(fast.oil_sensor_c-55,1)
        self.assertLess(slow.oil_sensor_c-55,.1)
        self.assertAlmostEqual(fast.oil_original_sensor_c,slow.oil_sensor_c,places=10)
        self.assertAlmostEqual(fast.current_a,slow.current_a,places=12)
        self.assertAlmostEqual(fast.speed_ratio,slow.speed_ratio,places=12)

    def test_thermal_scale_must_be_finite_and_positive(self):
        for scale in (0,-1,float('nan'),float('inf')):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                DemoModel({'time_scale':scale})


class TestAcousticLoop(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.app=AcousticApplication(self.tmp.name)
        self.app.load(str(SCENARIO))
        self.app.start()

    def tearDown(self):
        self.app.close()
        self.tmp.cleanup()

    def advance(self,seconds):
        for _ in range(round(seconds*10)):
            self.app.step()

    def source(self,action):
        return self.app.submit_operation({"target_asset_id":"sensor_attack_node","action_type":action})

    def test_normal_and_original_amplitude_do_not_trip(self):
        self.advance(4)
        self.assertIsNone(self.app.acoustic.cutoff_s)
        self.source("start_weak")
        self.advance(10)
        self.assertTrue(self.app.acoustic.powered)
        self.assertFalse(self.app.acoustic.trip_latched)
        self.assertLess(self.app.acoustic.feature["max_ratio"],2)

    def test_strong_trip_uses_three_received_windows_and_round_trip_path(self):
        self.advance(3)
        self.source("start_strong")
        self.advance(12)
        self.assertFalse(self.app.acoustic.powered)
        self.assertTrue(self.app.acoustic.trip_latched)
        view=self.app.get_view('acoustic')
        oil_state=self.app.engine.get_device('oil_temp').state
        self.assertEqual(view['oil_c'],oil_state['last_value'])
        self.assertEqual(view['oil_sample_time_s'],oil_state['last_sample_time_us']/1e6)
        trip=next(e for e in self.app.events if e["kind"]=="threshold_trip")
        issued=next(e for e in self.app.events if e["kind"]=="stop_command_issued")
        self.assertAlmostEqual(issued["observed_time_s"]-trip["observed_time_s"],1)
        self.assertGreater(self.app.acoustic.cutoff_s,issued["observed_time_s"])
        messages=[e for e in self.app.events if e["kind"]=="message_delivered"]
        commands=[(e['src'],e['dst']) for e in messages if e['business_type']=='command']
        self.assertEqual(commands,[("station","mc"),("mc","ctl_cool"),("ctl_cool","cool")])
        completed=[(e['src'],e['dst']) for e in messages if e['payload'].get('status')=='completed']
        self.assertEqual(completed,[("cool","ctl_cool"),("ctl_cool","mc"),("mc","station")])
        self.assertEqual({e['request_id'] for e in messages},{trip['request_id']})
        self.app.recorder.flush()
        reader=RunReader(str(self.app.recorder.output_dir))
        observed=[e for e in reader.read_all('acoustic_path') if e['kind']=='measurement_received' and e['observed_time_s']<=trip['observed_time_s']]
        self.assertEqual([e['consecutive_windows'] for e in observed[-3:]],[1,2,3])
        self.assertTrue(any(e['kind']=='oil_measurement_received' for e in reader.read_all('acoustic_path')))

    def test_blocking_control_link_prevents_cutoff_despite_alarm(self):
        self.app.engine._network.set_link_effect('L-mc-cool',{'extra_loss_rate':1})
        self.source('start_strong')
        self.advance(10)
        self.assertTrue(self.app.acoustic.trip_latched)
        self.assertTrue(self.app.acoustic.powered)
        self.assertIsNone(self.app.acoustic.cutoff_s)
        self.assertFalse(any(e.get('dst')=='cool' for e in self.app.events))

    def test_missing_fan_telemetry_prevents_threshold_decision(self):
        self.app.engine._network.set_link_effect('L-fan-monitor-mc',{'extra_loss_rate':1})
        self.source('start_strong')
        self.advance(10)
        self.assertGreater(self.app.acoustic.feature['max_ratio'],2)
        self.assertFalse(self.app.acoustic.trip_latched)
        self.assertTrue(self.app.acoustic.powered)

    def test_source_off_does_not_restart_and_reset_requires_normal_window(self):
        self.source('start_strong')
        self.advance(10)
        self.assertEqual(self.source('reset_protection')['status'],'rejected')
        self.source('stop_attack')
        self.advance(3)
        self.assertFalse(self.app.acoustic.powered)
        preview=self.app.get_view('acoustic')['attack_preview']
        self.assertIsNotNone(preview)
        self.assertLess(preview['time_s'],self.app.acoustic.cutoff_s)
        self.assertEqual(len(preview['wave']),80)
        self.assertTrue(any(abs(row[5])>1 for row in preview['wave']))
        self.app.submit_operation({'target_asset_id':'cool','action_type':'start'})
        self.advance(1)
        self.assertFalse(self.app.acoustic.powered)
        self.assertEqual(self.source('reset_protection')['status'],'accepted')
        self.app.submit_operation({'target_asset_id':'cool','action_type':'start'})
        self.advance(1)
        self.assertTrue(self.app.acoustic.powered)

    def test_old_scene_still_loads_in_extension_application(self):
        self.app.load(str(SCENARIO.parent.parent/'scenarios/substation-sensor-attack.json'))
        self.assertIsNone(self.app.acoustic)
        self.app.start()
        self.app.step()
        self.assertFalse(self.app.get_view('acoustic')['available'])

    def test_station_step_owns_rule_without_framework_operation_or_observer(self):
        self.app.engine.message_handler=None
        self.source('start_strong')
        with mock.patch.object(self.app.engine,'submit_business_operation',side_effect=AssertionError('应用层不得代发停机')):
            self.advance(10)
        station=self.app.engine.get_device('station')
        self.assertIsInstance(station,AcousticStation)
        self.assertTrue(station.state['trip_latched'])
        self.assertFalse(self.app.acoustic.powered)
        issued=next(e for e in self.app.events if e['kind']=='stop_command_issued')
        self.assertEqual(issued['owner_device_id'],'station')
        self.assertEqual(issued['emitted_from'],'AcousticStation.step')
        self.assertTrue(any(d['request_id']==issued['request_id'] for d in station._last_dispatched))

    def test_disabled_station_step_prevents_rule_and_command(self):
        self.source('start_strong')
        with mock.patch.object(self.app.engine.get_device('station'),'step',return_value=[]):
            self.advance(10)
        self.assertGreater(self.app.acoustic.feature['max_ratio'],2)
        self.assertTrue(self.app.acoustic.powered)
        self.assertFalse(self.app.acoustic.trip_latched)
        self.assertFalse(any(e['kind']=='threshold_trip' for e in self.app.events))

    def test_station_operates_in_engine_without_application_scheduler(self):
        config=load_scenario(str(SCENARIO))
        engine=AcousticEngine()
        engine.build(config.devices,config.links,config.environment)
        model=DemoModel({'time_scale':360})
        for device in engine._devices.values():
            if device.spec.device_type.startswith('acoustic_'):
                device.model=model
        model.response.switch(.9,0)
        emitted=[]
        for i in range(100):
            emitted.extend(engine.step(i*100000,100000).messages)
        self.assertFalse(model.powered)
        self.assertTrue(engine.get_device('station').state['trip_latched'])
        self.assertTrue(any(m.sender_id=='station' and m.receiver_id=='mc' and m.business_type=='command'
                            for m in emitted))

    def test_fan_status_does_not_replace_two_business_measurement_sources(self):
        self.advance(4)
        telemetry=self.app.engine.get_device('mc').build_telemetry()
        self.assertEqual(set(telemetry['sources']),{'mu','mu_2'})
        self.assertIn('vibration',telemetry['fan_sample'])
        self.assertGreater(self.app.engine.get_device('station').last_window_s,0)

    def test_topology_preserves_latest_main_template(self):
        template_path=SCENARIO.parent.parent/'scenarios/substation-sensor-attack.json'
        template=json.loads(template_path.read_text(encoding='utf-8'))
        scenario=json.loads(SCENARIO.read_text(encoding='utf-8'))
        template_devices={d['device_id']:d for d in template['devices']}
        devices={d['device_id']:d for d in scenario['devices']}
        self.assertEqual(set(devices),set(template_devices))
        self.assertTrue({'mu_2','ct_current_2','time_svc_2','diff_prot','spectrum_detect_node'}.issubset(devices))
        for key,device in template_devices.items():
            self.assertEqual(devices[key]['layout'],device['layout'])
        links={l['link_id']:l for l in scenario['links']}
        for link in template['links']:
            self.assertEqual(links[link['link_id']],link)
        self.assertEqual(set(links)-{l['link_id'] for l in template['links']},{'L-fan-monitor-mc'})
        self.assertEqual(links['L-fan-monitor-mc']['parameters']['protocol'],'mms')
        layout=copy.deepcopy(scenario['layout'])
        provenance=layout.pop('acoustic_template')
        self.assertEqual(provenance['sha256'],hashlib.sha256(template_path.read_text(encoding='utf-8').encode('utf-8')).hexdigest())
        process=next(n for n in layout['deployment']['networks'] if n['id']=='process')
        process['link_ids'].remove('L-fan-monitor-mc')
        next(c for c in process['connections'] if c['device_id']=='mc')['business_ports'].remove('fan_mu')
        self.assertEqual(layout,template['layout'])


class TestAcousticServer(unittest.TestCase):
    def test_acoustic_entry_lists_only_its_scene_without_changing_shared_server(self):
        with tempfile.TemporaryDirectory() as folder:
            server=AcousticServer(output_root=folder)
            shared=PresentationServer(output_root=folder)
            try:
                status,payload,_=server.handle_request('GET','/api/scenarios')
                self.assertEqual(status,200)
                self.assertEqual([(s['scenario_id'],s['name']) for s in payload['scenarios']],
                                 [('substation-acoustic-demo','声波干扰联动（仿真）')])
                self.assertIn('substation-em-attack',shared._scenario_paths)
                self.assertIn('substation-network-attack',shared._scenario_paths)
                self.assertNotIn('substation-acoustic-demo',shared._scenario_paths)
            finally:
                server.close()
                shared.close()

    def test_addon_routes_and_bound_advancement(self):
        with tempfile.TemporaryDirectory() as folder:
            server=AcousticServer(output_root=folder)
            try:
                status,payload,_=server.handle_request('GET','/')
                self.assertEqual(status,200)
                self.assertIn('data-view="acoustic"',payload['__raw__'])
                self.assertIn('/acoustic.js',payload['__raw__'])
                self.assertEqual(server.handle_request('GET','/acoustic.js')[0],200)
                def post(route,body):
                    return server.handle_request('POST',route,json.dumps(body).encode())
                self.assertEqual(post('/api/acoustic/advance',{'seconds':61})[0],400)
                self.assertEqual(post('/api/acoustic/advance',{'seconds':1,'run_id':'stale'})[0],409)
                _,created,_=post('/api/runs',{'scenario_id':'substation-acoustic-demo'})
                status,payload,_=post('/api/acoustic/advance',{'seconds':2,'run_id':created['run_id']})
                self.assertEqual(status,200)
                self.assertAlmostEqual(payload['data']['time_s'],2)
                self.assertEqual(server.application.status,'paused')
                self.assertTrue(payload['data']['powered'])
            finally:
                server.close()


if __name__=='__main__':
    unittest.main()
