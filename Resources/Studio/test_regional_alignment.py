import unittest
import numpy as np
from regional_alignment import fit_alignment,warp_points


class RegionalAlignmentTests(unittest.TestCase):
    def setUp(self):
        self.points=np.array([[.3,.25],[.7,.25],[.5,.45],[.4,.6],[.6,.6],[.5,.85]])

    def test_rotation_scale_and_translation(self):
        matrix=np.array([[.85,-.12],[.12,.85]]);offset=np.array([.02,.06])
        fit=fit_alignment(self.points,self.points@matrix+offset)
        query=np.array([[.42,.4],[.63,.55]])
        np.testing.assert_allclose(warp_points(query,fit),query@matrix+offset,atol=1e-6)

    def test_local_feature_correspondence(self):
        reference=self.points.copy();reference[2]+=[.04,-.06];reference[3:5,1]-=.025
        np.testing.assert_allclose(warp_points(self.points,fit_alignment(self.points,reference)),reference,atol=1e-6)

    def test_repeated_points_rejected(self):
        with self.assertRaises(ValueError):fit_alignment(np.zeros((6,2)),self.points)

    def test_collinear_points_rejected(self):
        with self.assertRaises(ValueError):fit_alignment(np.c_[np.arange(6)/6,np.arange(6)/6],self.points)

    def test_nonfinite_and_outside_points_rejected(self):
        for value in (float('nan'),float('inf'),1.1,-.1):
            bad=self.points.copy();bad[0,0]=value
            with self.assertRaises(ValueError):fit_alignment(bad,self.points)
