import sys
import os
import time
from functools import partial

import numpy as np
import pybullet as p
if os.environ.get('ROS_DISTRO'):
    import rospy


from cairo_simulator.core.context import SawyerSimContext

from cairo_planning.collisions import DisabledCollisionsContext
from cairo_planning.local.interpolation import interpolate_5poly
from cairo_planning.local.curve import JointTrajectoryCurve


def main():

    sim_context = SawyerSimContext()
    sim = sim_context.get_sim_instance()
    _ = sim_context.get_logger()
    state_space = sim_context.get_state_space()
    svc = sim_context.get_state_validity()
    sawyer_robot = sim_context.get_robot()
    _ = sawyer_robot.get_simulator_id()
    _ = sim_context.get_sim_objects(['Ground'])[0]
    
    sawyer_robot.move_to_joint_pos([0, 0, 0, 0, 0, 0, 0])
    time.sleep(1)

    valid_samples = []
    # Disabled collisions during planning with certain eclusions in place.
    with DisabledCollisionsContext(sim, [], []):
        while True:
            sample = state_space.sample()
            if svc.validate(sample):
                valid_samples.append(sample)
            if len(valid_samples) >= 1:
                break

    # Generate local plan between two points and execute local plan.
    start_pos = [0]*7
    sawyer_robot.move_to_joint_pos(start_pos)
    time.sleep(.1)
    # This interpolate_5poly will be replaced with the sequence of points generated by a planner.
    qt, _, _ = interpolate_5poly(
        np.array(start_pos[0:7]), np.array(valid_samples[0]), 50)
    jtc = JointTrajectoryCurve()
    traj = jtc.generate_trajectory(qt, move_time=2)
    sawyer_robot.execute_trajectory(traj)
    # Loop until someone shuts us down
    try:
        while True:
            sim.step()
    except KeyboardInterrupt:
        p.disconnect()
        sys.exit(0)


if __name__ == "__main__":
    main()