import copy
import unittest
from joint_config import validate_joint_config


class JointConfigTests(unittest.TestCase):
    def fixture(self):
        roles=['Hips','Head']+[s+p for s in ('Left','Right') for p in ('Arm','ForeArm','Hand','Leg','Shin','Foot')]
        return dict(coordinate_space='gltf_y_up',joints=[dict(name=f'bone_{i}',position=[0.,i*.01,0.],role=r) for i,r in enumerate(roles)])

    def test_complete_mapping_and_coordinates_roundtrip(self):
        source=self.fixture();self.assertEqual(validate_joint_config(source),source)

    def test_nonfinite_or_boolean_coordinates_rejected(self):
        for value in (float('inf'),float('nan'),True):
            source=self.fixture();source['joints'][0]['position'][0]=value
            with self.assertRaises(ValueError):validate_joint_config(source)

    def test_duplicate_names_or_anatomical_roles_rejected(self):
        for field in ('name','role'):
            source=self.fixture();source['joints'][1][field]=source['joints'][0][field]
            with self.assertRaises(ValueError):validate_joint_config(source)

    def test_missing_limb_and_unknown_coordinate_space_rejected(self):
        source=self.fixture();source['joints'].pop()
        with self.assertRaises(ValueError):validate_joint_config(source)
        source=self.fixture();source['coordinate_space']='screen_pixels'
        with self.assertRaises(ValueError):validate_joint_config(source)

    def test_malformed_point_rejected(self):
        source=self.fixture();source['joints'][0]=None
        with self.assertRaises(ValueError):validate_joint_config(source)
