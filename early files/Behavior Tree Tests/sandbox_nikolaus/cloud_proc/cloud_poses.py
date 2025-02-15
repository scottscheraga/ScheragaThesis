from scipy.spatial import ConvexHull
from sklearn import linear_model
from sklearn.decomposition import PCA
import numpy as np
import open3d
import pmath


class Cloud_Poses:
    def get_cloud_pose(self, clouds, frame='camera', use_centroid=False):
        """
        Finds the origin of the object cloud.

        Parameters
        ----------
        object_cloud: [n,3] ndarray
            Sequence of points in object cloud.

        Return
        ------
        trans: [4,4] ndarray
            The transformation matrix representing the position and orientation of the object. \
            The axis lies at the highest point on the object, with the y_axis aligned with the \
            principle axis of the cloud.
            [r11,r12,r13,tx]\n
            [r21,r22,r23,ty]\n
            [r31,r32,r33,tz]\n
            [ 0 , 0 , 0 , 1]
        """
        # Single Cloud
        if type(clouds) is not list:
            cloud_list = [clouds]
        # List of Clouds
        else:
            cloud_list = clouds

        poses = []
        # For each cloud in the list
        for cloud in cloud_list:
            pca = PCA(n_components=3)
            pca_cloud = pca.fit_transform(cloud)
            hull = ConvexHull(pca_cloud[:, :2])

            max_z = pca_cloud[:, 2].min()
            min_z = pca_cloud[:, 2].max()
            if pca.components_[2, 2] < 0:
                direction = -1
                max_z = pca_cloud[:, 2].max()
                min_z = pca_cloud[:, 2].min()

            # Find min volume
            volume_bb = 10000000
            for simplex in hull.simplices:
                u = pca_cloud[simplex[0], :2]
                v = pca_cloud[simplex[1], :2]
                slope = (v[1] - u[1])/(v[0] - u[0])
                theta = np.arctan(slope)
                rot = np.array([[np.cos(theta), -np.sin(theta), 0.0],
                                [np.sin(theta), np.cos(theta), 0.0], [0.0, 0.0, 1.0]])
                pc = pca_cloud.dot(rot)
                min_x = pc[:, 0].min()
                max_x = pc[:, 0].max()
                min_y = pc[:, 1].min()
                max_y = pc[:, 1].max()
                vol = np.abs(min_x-max_x)*np.abs(min_y-max_y)
                if vol < volume_bb:
                    vertices = np.zeros((4, 3))
                    origin = np.array([min_x, min_y, max_z])
                    if (max_x-min_x > max_y-min_y):
                        vertices[0, :] = np.array(
                            [max_x, min_y, max_z]) - origin
                        vertices[1, :] = np.array(
                            [min_x, max_y, max_z]) - origin
                    else:
                        vertices[1, :] = np.array(
                            [max_x, min_y, max_z]) - origin
                        vertices[0, :] = np.array(
                            [min_x, max_y, max_z]) - origin
                    x_offset = (pc[:, 0].max() + pc[:, 0].min())/2.0
                    y_offset = (pc[:, 1].max() + pc[:, 1].min())/2.0
                    vertices[2, :] = np.array([min_x, min_y, min_z]) - origin
                    vertices = vertices.dot(np.linalg.inv(rot))
                    if frame == 'camera':
                        translation = np.array([x_offset, y_offset, max_z])
                    if frame == 'base':
                        translation = np.array([x_offset, y_offset, min_z])
                    translation = translation.dot(np.linalg.inv(rot))
                    volume_bb = vol

            rot = np.array([vertices[0]/np.linalg.norm(vertices[0]), vertices[1] /
                            np.linalg.norm(vertices[1]), vertices[2]/np.linalg.norm(vertices[2])]).T
            rot_pca = pca.components_.T
            # Init pose
            pose = np.eye(4)
            # Set the rotation
            pose[:3, :3] = rot_pca.dot(rot)
            # Set the translation
            # If using the centroid/COM/unweighted average point as center
            if use_centroid:
                pose[:3, 3] = self.cloud_centroid(cloud)
            else:
                # If using the PCA translation
                pose[:3, 3] = pca.inverse_transform(translation)
            pose[:3, 1] = np.cross(pose[:3, 2], pose[:3, 0])

            pose = pmath.rotate_pose(pose, [0, 0, 1.5707], dir_pose='self')
            if frame == 'base':
                pose = pmath.rotate_pose(
                    pose, [0, 3.14159, 0], dir_pose='self')

            poses.append(pose)

        if type(clouds) is not list:
            return poses[0]
        else:
            return poses

    def get_cloud_width(self, object_cloud, pose=None):
        """
        Finds width of object

        Parameters
        ----------
        object_cloud: [n,3] ndarray
            Sequence of points in object cloud.
        transform: [4,4] ndarray
            Transformation matrix to find width about. The gripper grasps along the x-axis, \
            and so the width will be generated by the positions of the maximum distance along \
            this axis.
            [r11,r12,r13,tx]\n
            [r21,r22,r23,ty]\n
            [r31,r32,r33,tz]\n
            [ 0 , 0 , 0 , 1]

        Return
        ------
        width: float
            Width of object (m).
        """
        pca = PCA(n_components=3)
        if pose is None:
            object_cloud_pca = pca.fit_transform(object_cloud)
            width = float(object_cloud_pca[:, 1].max(
            ) - object_cloud_pca[:, 1].min())
        else:
            object_cloud_AA = pmath.transform_points(
                object_cloud, np.linalg.inv(pose))
            width = object_cloud_AA[:, 0].max() - object_cloud_AA[:, 0].min()

        return width

    def shift_pose_to_grasp(self, cloud, object_pose, object_width, step_size=0.005, min_clearance=0.001, rtnShift=False, output=[]):
        """
        Shifts tranform from top of object to position for grasping object

        Parameters
        ----------
        cloud: [n,3] ndarray
            Point cloud.
        pose: [4,4] ndarray
            Object transformation matrix.
        object_width: float
            Width of the object cloud for the grasp.
        step_size: float (optional)
            Amount that gripper steps back up through the point cloud each loop
        min_clearance: float (optional)
            Minimun amount of clearance above extraneous points the gripper will grasp from
        Returns
        -------
        transform: [4,4] ndarray
            New transformation matrix for graping object.
        """
        finger_width = object_width + 0.010

        self.logger.debug(str(["Starting `shift_pose_to_grasp` with rtnShift =", rtnShift]))

        # Shift pose towards object by finger offset length
        in_shift = self.hand.finger_length + \
            (0.055 - (0.055**2.0 - (object_width/2)**2.0)**0.5)
        grasp_pose = pmath.translate_pose(
            object_pose, [0, 0, in_shift], dir_pose='self')

        # Create gripper boxes and check if cloud points are inside. Loop thru until boxes clear points
        gripper_boxes, finger_boxes = self.get_gripper_boxes(
            grasp_pose, finger_width)
        out_shift = 0

        self.logger.debug(
            str(["There are", len(gripper_boxes), " gripper boxes to check"]))

        for idx, box in enumerate(gripper_boxes):
            while self.is_point_in_box(gripper_boxes[idx], cloud).any():
                grasp_pose = pmath.translate_pose(
                    grasp_pose, [0, 0, -step_size], dir_pose='self')
                out_shift += step_size
                gripper_boxes, finger_boxes = self.get_gripper_boxes(
                    grasp_pose, finger_width)
                if 'all' in output:
                    view = self.PC_Viewer()
                    view.add_cloud(cloud)
                    view.add_gripper_boxes(gripper_boxes, finger_boxes)
                    view.show(view='base')
                # Check if gripper has shifted out past the object and can no longer grasp it
                if out_shift >= in_shift-min_clearance-0.002:
                    self.logger.debug('End Of Shift Pose, No Grasp Found')
                    return None

        # Shift one last time for minimun clearance and return
        grasp_pose = pmath.translate_pose(
            grasp_pose, [0, 0, -min_clearance], dir_pose='self')
        if rtnShift:
            self.logger.debug(
                str(["Grasp Shift Return:", type(grasp_pose), type(in_shift - out_shift)]))
            return grasp_pose, (in_shift - out_shift)
        else:
            return grasp_pose

    def get_gripper_boxes(self, pose, width, output=False):
        """
        Gives the vertices of the bounding boxes of the gripper for the proposed grasp pose.

        Parameters
        ----------
        transform: [4,4] ndarray
            transform[:3,:3] corrisponds to the rotation matrix of the grasp orientation and transform[:3,4] \
            corrisponds to the translation of the grasp. This transform can be generated from get_object_transform().
        width: float
            Width of the object cloud for the grasp.

        Returns
        -------
        gripper_boxes: [4,[8,3]] ndarray
            The 4 arrays corrispond to the 4 boxes that describe the shape of the gripper. \n
            [right finger, left finger, linkage, shell]\n
            Each box is defined by its 8 vertices: \n
            [min_x,min_y,max_z]\n
            [max_x,min_y,max_z]\n
            [min_x,max_y,max_z]\n
            [max_x,max_y,max_z]\n
            [min_x,min_y,min_z]\n
            [max_x,min_y,min_z]\n
            [min_x,max_y,min_z]\n
            [max_x,max_y,min_z]
        finger_boxes: [2,[8,3]] ndarray
            The 2 arrays corrispond to the 2 boxes that describe the fingertips of the gripper \n
            [right finger, left finger]\n
            Each box is defined by its 8 vertices: \n
            [min_x,min_y,max_z]\n
            [max_x,min_y,max_z]\n
            [min_x,max_y,max_z]\n
            [max_x,max_y,max_z]\n
            [min_x,min_y,min_z]\n
            [max_x,min_y,min_z]\n
            [min_x,max_y,min_z]\n
            [max_x,max_y,min_z]
        """

        # Check if width is greater then max gripper width
        if width > 0.108:
            if output == True:
                print("Object width is greater than max gripper width")
            return None, None

        # Create direction vectors for constructing boxes
        pose = pmath.rotate_pose(pose, [0, 0, 1.57], dir_pose='self')
        start_point = np.hstack((np.zeros((3, 3)), np.ones((3, 1))))
        end_point = np.hstack((np.eye(3), np.ones((3, 1))))

        start_pose = pose.dot(start_point.T)
        end_pose = pose.dot(end_point.T)

        x_vec = end_pose[:3, 0] - start_pose[:3, 0]
        x_vec = x_vec/np.linalg.norm(x_vec)

        y_vec = end_pose[:3, 1] - start_pose[:3, 1]
        y_vec = y_vec/np.linalg.norm(y_vec)

        z_vec = end_pose[:3, 2] - start_pose[:3, 2]
        z_vec = z_vec/np.linalg.norm(z_vec)

        # Create externally defined parameters
        finger_length = self.hand.finger_length
        finger_width = self.hand.finger_width_outer
        finger_depth = self.hand.finger_depth

        finger_to_pos = 0.055-((0.055**2.0)-(width/2.0)**2.0)**0.5
        pivot_width_from_center = ((width/2.0)+0.048)

        # Get position from pose
        pos = pose[:3, 3]

        # Create the gripper boxes
        fingerbox0 = np.zeros((8, 3))
        fingerbox1 = np.zeros((8, 3))
        box0 = np.zeros((8, 3))
        box1 = np.zeros((8, 3))
        box2 = np.zeros((8, 3))
        box3 = np.zeros((8, 3))
        # Right finger box
        fingerbox0[0] = pos + ((finger_depth/2.0)*x_vec) - \
            (((width/2.0)+0.003)*y_vec) - (finger_to_pos*z_vec)
        fingerbox0[1] = pos + ((finger_depth/2.0)*x_vec) - \
            ((width/2.0)*y_vec) - (finger_to_pos*z_vec)
        fingerbox0[2] = fingerbox0[0] - ((0.031+finger_length)*z_vec)
        fingerbox0[3] = fingerbox0[1] - ((0.031+finger_length)*z_vec)
        fingerbox0[4] = fingerbox0[0] - (finger_depth*x_vec)
        fingerbox0[5] = fingerbox0[1] - (finger_depth*x_vec)
        fingerbox0[6] = fingerbox0[2] - (finger_depth*x_vec)
        fingerbox0[7] = fingerbox0[3] - (finger_depth*x_vec)
        # Left finger box
        fingerbox1[0] = pos + ((finger_depth/2.0)*x_vec) + \
            (((width/2.0)+0.003)*y_vec) - (finger_to_pos*z_vec)
        fingerbox1[1] = pos + ((finger_depth/2.0)*x_vec) + \
            ((width/2.0)*y_vec) - (finger_to_pos*z_vec)
        fingerbox1[2] = fingerbox1[0] - ((0.031+finger_length)*z_vec)
        fingerbox1[3] = fingerbox1[1] - ((0.031+finger_length)*z_vec)
        fingerbox1[4] = fingerbox1[0] - (finger_depth*x_vec)
        fingerbox1[5] = fingerbox1[1] - (finger_depth*x_vec)
        fingerbox1[6] = fingerbox1[2] - (finger_depth*x_vec)
        fingerbox1[7] = fingerbox1[3] - (finger_depth*x_vec)
        # Right gripper finger box
        box0[0] = pos + ((finger_depth/2.0)*x_vec) - \
            (((width/2.0)+finger_width)*y_vec) - (finger_to_pos*z_vec)
        box0[1] = pos + ((finger_depth/2.0)*x_vec) - \
            ((width/2.0)*y_vec) - (finger_to_pos*z_vec)
        box0[2] = box0[0] - ((0.031+finger_length)*z_vec)
        box0[3] = box0[1] - ((0.031+finger_length)*z_vec)
        box0[4] = box0[0] - (finger_depth*x_vec)
        box0[5] = box0[1] - (finger_depth*x_vec)
        box0[6] = box0[2] - (finger_depth*x_vec)
        box0[7] = box0[3] - (finger_depth*x_vec)
        # Left gripper finger box
        box1[0] = pos + ((finger_depth/2.0)*x_vec) + \
            (((width/2.0)+finger_width)*y_vec) - (finger_to_pos*z_vec)
        box1[1] = pos + ((finger_depth/2.0)*x_vec) + \
            ((width/2.0)*y_vec) - (finger_to_pos*z_vec)
        box1[2] = box1[0] - ((0.031+finger_length)*z_vec)
        box1[3] = box1[1] - ((0.031+finger_length)*z_vec)
        box1[4] = box1[0] - (finger_depth*x_vec)
        box1[5] = box1[1] - (finger_depth*x_vec)
        box1[6] = box1[2] - (finger_depth*x_vec)
        box1[7] = box1[3] - (finger_depth*x_vec)
        # Gripper linkage box
        box2[0] = pos + (0.0115*x_vec) - (pivot_width_from_center *
                                          y_vec) - ((0.031+finger_length+finger_to_pos)*z_vec)
        box2[1] = box2[0] + (2.0*pivot_width_from_center*y_vec)
        box2[2] = pos + (0.0115*x_vec) - (pivot_width_from_center *
                                          y_vec) - ((0.116+finger_length)*z_vec)
        box2[3] = box2[2] + (2.0*pivot_width_from_center*y_vec)
        box2[4] = box2[0] - (0.023*x_vec)
        box2[5] = box2[1] - (0.023*x_vec)
        box2[6] = box2[2] - (0.023*x_vec)
        box2[7] = box2[3] - (0.023*x_vec)
        # Gipper body box
        box3[0] = pos + (0.033*x_vec) - (0.05*y_vec) - \
            ((0.075+finger_length)*z_vec)
        box3[1] = pos + (0.033*x_vec) + (0.05*y_vec) - \
            ((0.075+finger_length)*z_vec)
        box3[2] = box3[0] - (0.14*z_vec)
        box3[3] = box3[1] - (0.14*z_vec)
        box3[4] = box3[0] - (0.08*x_vec)
        box3[5] = box3[1] - (0.08*x_vec)
        box3[6] = box3[2] - (0.08*x_vec)
        box3[7] = box3[3] - (0.08*x_vec)

        return np.array((box0, box1, box2, box3)), np.array((fingerbox0, fingerbox1))