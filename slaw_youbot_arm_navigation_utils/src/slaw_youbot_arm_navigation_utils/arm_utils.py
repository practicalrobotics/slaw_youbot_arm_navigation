import rospy
import math
from brics_actuator.msg import JointVelocities, JointValue
from geometry_msgs.msg import PointStamped, Point
from slaw_youbot_arm_navigation_srvs.srv import SimpleIkSolver, SimpleIkSolverRequest
import numpy as np

arm_base_frame = rospy.get_param('arm_base_link', '/arm_base_link')
arm_rot_offset = rospy.get_param('arm_rot_offset')

iks = rospy.ServiceProxy('/arm_1/simple_ik_server', SimpleIkSolver)

joints = rospy.get_param('joints')
joint_names = rospy.get_param("joint_names")


def create_arm_up():
    conf = []
    for n in joint_names:
        conf.append(joints[n]['straight'])
    conf[0] -= arm_rot_offset
    return conf


def create_tucked():
    conf = []
    for n in joint_names:
        conf.append(joints[n][joints[n]['tucked']])
    return conf


def interpolate_joint_speeds(start_conf, end_conf, steps):
    step_size = (end_conf - start_conf) / steps
    path = []
    for i in xrange(steps):
        path.append(start_conf + i * step_size)
    path.append(end_conf)
    return path


def wrap_angle(angle):
    while angle > math.pi:
        angle -= math.pi * 2.0
    while angle < -math.pi:
        angle += math.pi * 2.0
    return angle


def limit_joint_5(ang):
    while ang < joints['arm_joint_5']['min']:
        ang += math.pi
    while ang > joints['arm_joint_5']['max']:
        ang -= math.pi
    return ang


def get_forward_angle_joint_5(straight, angle):
    angle = wrap_angle(angle)
    ang = straight - angle

    # print straight, angle
    if 0 < angle < math.pi / 2:
        ang += math.pi
    if 0 > angle > -math.pi / 2:
        ang -= math.pi
    ang = limit_joint_5(ang)
    return ang


def limit_joint(joint, value):
    if joint in joints.keys():
        val_min = joints[joint]['min']
        val_max = joints[joint]['max']
        if value <= val_min:
            return val_min + 0.001
        if value >= val_max:
            return val_max - 0.001
    return value


def create_null_velocity(unit):
    msg = JointVelocities()
    time = rospy.Time.now()
    for joint_name in joint_names:
        j = JointValue()
        j.timeStamp = time
        j.joint_uri = joint_name
        j.unit = unit
        msg.velocities.append(j)
    return msg


def configuration_to_array(configuration_msg, current_configuration):
    conf = [x for x in current_configuration]
    if not configuration_msg.arm_joint_1 == 0:
        conf[0] = configuration_msg.arm_joint_1
    if not configuration_msg.arm_joint_2 == 0:
        conf[1] = configuration_msg.arm_joint_2
    if not configuration_msg.arm_joint_3 == 0:
        conf[2] = configuration_msg.arm_joint_3
    if not configuration_msg.arm_joint_4 == 0:
        conf[3] = configuration_msg.arm_joint_4
    if not configuration_msg.arm_joint_5 == 0:
        conf[4] = configuration_msg.arm_joint_5
    return conf


def call_ik_solver(goal_point, side, horizontal=False, endlink_angle=0.0, endeffector_offset=0.0):
    req = SimpleIkSolverRequest()
    # rospy.loginfo("Ik side %s", side)
    req.position = side
    req.point = PointStamped()
    req.point.point = goal_point
    req.point.header.frame_id = arm_base_frame
    req.point.header.stamp = rospy.Time()
    req.endeffector_offset = endeffector_offset
    req.endlink_angle = endlink_angle
    req.horizontal = horizontal
    resp = None
    try:
        resp = iks(req)
    except rospy.ServiceException, e:
        rospy.logerr("Service did not process request: %s", str(e))
    if resp is not None and len(resp.joints) > 0:
        return np.array(resp.joints)
    return None

LOWER_CENTER = [0.25, 0, -0.057]
CENTER = [0.27, 0, -0.077]
CENTER_ANGLE = [0.5, 0.1]
DROP = [0.37047634135746554, 1.3281082943050848, -1.1753640594875492, 2.965021872122891, 2.7544245448727414]

def create_backplate_trajectory(backplate_pose, z_offset=0.0):
    traj = []
    if backplate_pose == 'LOWER_CENTER':
        p = Point(*LOWER_CENTER)
        p.z += z_offset
        conf = call_ik_solver(p, side='back')
        conf[0] = 0.4
        conf[4] += math.pi/2.
    elif backplate_pose == 'CENTER':
        conf = call_ik_solver(Point(*CENTER), side='back')
    elif backplate_pose == 'LEFT':
        conf = call_ik_solver(Point(*CENTER), side='back')
        conf[0] = CENTER_ANGLE[0]
    elif backplate_pose == 'DROP':
        conf = DROP
    else:  # backplate_pose == 'RIGHT':
        conf = call_ik_solver(Point(*CENTER), side='back')
        conf[0] = CENTER_ANGLE[1]
    traj.append(conf)
    return traj


def create_place_trajectory(x, y, z, obj, side, theta=0.0, last_joint_tolerance_in_degree=15):
    place_point = Point(x, y, z)
    arm_config = []
    for i in range(last_joint_tolerance_in_degree):
        ang = math.radians(i)
        arm_config = call_ik_solver(place_point, side, horizontal=False, endlink_angle=ang, endeffector_offset=0.0)
        if arm_config is not None:
            break

    if arm_config is None:
        return None

    arm_config_pregrasp = []
    place_point.z += 0.05
    for i in range(last_joint_tolerance_in_degree):
        ang = math.radians(i)
        arm_config_pregrasp = call_ik_solver(place_point, side, horizontal=False, endlink_angle=ang, endeffector_offset=0.0)
        if arm_config_pregrasp is not None:
            break
        ang = math.radians(-i)
        arm_config_pregrasp = call_ik_solver(place_point, side, horizontal=False, endlink_angle=ang, endeffector_offset=0.0)
        if arm_config_pregrasp is not None:
            break

    if arm_config_pregrasp is None:
        arm_config_pregrasp = arm_config

    arm_config[4] = get_forward_angle_joint_5(arm_config[4], -theta)
    arm_config_pregrasp[4] = arm_config[4]

    return [arm_config_pregrasp, arm_config]


def forward_kinematics(configuration, side, horizontal=False, endlink_angle=0.0, endeffector_offset=0.0):
    last_joint_length = joints['arm_joint_4']['length'] + joints['arm_joint_5']['length'] + endeffector_offset
    len_joint_2 = joints['arm_joint_2']['length']
    len_joint_3 = joints['arm_joint_3']['length']
    angle_j2 = math.pi / 2. - (configuration[1] - joints['arm_joint_2']['straight'])
    angle_j3 = abs(joints['arm_joint_3']['straight'] - configuration[2]) - angle_j2

    projected_len_joint_2 = math.cos(angle_j2) * len_joint_2
    projected_len_joint_3 = math.cos(angle_j3) * len_joint_3

    dist = joints['arm_joint_1']['front_offset'] + projected_len_joint_2 + projected_len_joint_3
    if horizontal:
        endlink_angle = math.pi / 2.
    # if horizontal:
    dist += math.sin(endlink_angle) * last_joint_length

    offset = 0.
    if side == 'left':
        offset = math.pi / 2.
    if side == 'right':
        offset = -math.pi / 2.
    if side == 'back':
        offset = math.pi
    rotation_angle = configuration[0] - (joints['arm_joint_1']['straight'] - arm_rot_offset - offset)

    x = dist * math.cos(rotation_angle)
    y = -dist * math.sin(rotation_angle)

    height_diff = joints['arm_joint_1']['height']
    # if not horizontal:
    height_diff -= math.cos(endlink_angle) * last_joint_length

    z_diff = math.sin(angle_j2) * len_joint_2 - math.sin(angle_j3) * len_joint_3
    z = height_diff + z_diff
    ang = joints['arm_joint_5']['straight'] - configuration[4]
    if abs(endlink_angle) < math.pi / 8.:  # not horizontal:
        ang += rotation_angle
    # print "x, y, z = %.3f, %.3f, %.3f, ang = %.3f, side = %s" % (x, y, z, ang, side)
    return [x, y, z], ang