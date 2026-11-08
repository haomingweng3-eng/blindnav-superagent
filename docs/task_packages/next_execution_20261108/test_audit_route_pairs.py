"""Synthetic tests only; no SANPO or model execution."""
import csv
import gzip
import math
import tempfile
import unittest
from pathlib import Path
import audit_route_pairs as m

class Contracts(unittest.TestCase):
    def rows(self):
        common=dict(session='synthetic',condition='production',arm='mobilio',
                    safety_state='CLEAR',authorized_motion='STRAIGHT',danger_any='False',confirmed_clear='True')
        return [dict(common,frame='0',surface_angle_rad='0',occupancy_angle_rad=str(math.pi/6)),
                dict(common,frame='1',surface_angle_rad='',occupancy_angle_rad='0'),
                dict(common,frame='2',surface_angle_rad=str(2*math.pi),occupancy_angle_rad='0')]
    def source(self, folder, rows, name='test.csv'):
        p=Path(folder)/name
        op=gzip.open if name.endswith('.gz') else open
        with op(p,'wt',newline='',encoding='utf-8') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
        return p
    def test_circular_and_missing(self):
        with tempfile.TemporaryDirectory() as d:
            rs=m.load_rows(self.source(d,self.rows(),'test.csv.gz'),3)
            summary,detail,_=m.audit(rs)
            self.assertEqual(summary['totals']['different_angle_pairs'],1)
            self.assertEqual(summary['totals']['missing_angle_pairs'],1)
            self.assertEqual(summary['totals']['finite_angle_pairs'],2)
            self.assertAlmostEqual(detail[0]['difference_deg'],30)
            self.assertEqual(detail[2]['angle_pair_status'],'SAME_ANGLE')
    def test_duplicate_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rs=self.rows();rs.append(rs[0])
            with self.assertRaises(ValueError):m.load_rows(self.source(d,rs),0)
    def test_wrong_count_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):m.load_rows(self.source(d,self.rows()),481)
    def test_unknown_not_authorized(self):
        with tempfile.TemporaryDirectory() as d:
            rs=self.rows();rs[0].update(safety_state='UNKNOWN',authorized_motion='')
            result,_,_=m.audit(m.load_rows(self.source(d,rs),3))
            self.assertEqual(result['totals']['authorized'],2)
    def test_overlap_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rs=self.rows();rs[0]['danger_any']='True'
            with self.assertRaises(ValueError):m.load_rows(self.source(d,rs),3)
    def test_bad_enum_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            rs=self.rows();rs[0]['authorized_motion']='MAGIC'
            with self.assertRaises(ValueError):m.load_rows(self.source(d,rs),3)

if __name__=='__main__':unittest.main(verbosity=2)
