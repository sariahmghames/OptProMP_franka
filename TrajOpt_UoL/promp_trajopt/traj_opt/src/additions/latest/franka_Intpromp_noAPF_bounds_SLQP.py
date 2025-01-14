  #!/usr/bin/env python

# import sys
# class KineticImportsFix:
# 	def __init__(self, kinetic_dist_packages="/opt/ros/kinetic/lib/python2.7/dist-packages"):
# 		self.kinetic_dist_packages = kinetic_dist_packages

# 	def __enter__(self):
# 		sys.path.remove(self.kinetic_dist_packages)

# 	def __exit__(self, exc_type, exc_val, exc_tb):
# 		sys.path.append(self.kinetic_dist_packages)


#with KineticImportsFix():   
import os
import sys
import rospy
import numpy as np
from operator import itemgetter
import scipy.optimize as opt
from scipy.optimize import LinearConstraint, Bounds, NonlinearConstraint
from scipy.optimize import Bounds
from trajectory_msgs.msg import JointTrajectory, JointTrajectoryPoint
from sensor_msgs.msg import JointState
from geometry_msgs.msg import Pose, PoseArray
import basis as basis
import promps as promps
import phase as phase
import tf.transformations as tf_tran
import matplotlib.pyplot as plt
import franka_kinematics
from mpl_toolkits.mplot3d import Axes3D
import Franka_pushing_clusters as franka_decluster
import formulations as fm
import traj_opt    
from matplotlib import animation
from matplotlib.animation import FuncAnimation


franka_kin = franka_kinematics.FrankaKinematics()
franka_actions = franka_decluster.actions()

# This is the latest script in use to generate conditioned promp for franka, then the push_cost will create pushing actions
# the franka_intprompV2Opt.py has the pushing actions generated as IROS paper for Scara, pushing actions are not well generated as demos need to be collected 
# with franka for the specific picking task


class promp_pub(object):

	def __init__(self):
		#rospy.init_node('franka_intprompV2_publisher', anonymous=True)
		#self.rate = rospy.Rate(50)
		self.q0 = np.array([])
		#self.q0 = [0.0, 0.0, 0.0, -0.1, 0.0, 0.0, 0.8]  # comment out wen running sim
		self.q0 = [0.0, (-np.pi)/4, 0.0, (-np.pi)/2, 0.0, (np.pi)/3, 0.0]  ## works for end effector facing down , # it was [0.0, (-np.pi)/4, 0.0, (np.pi)/2, 0.0, (-np.pi)/3, 0.0]
		#self.q0 = [0.0, (-np.pi)/3, 0.0, (120*np.pi)/180, 0.0, (-np.pi)/3, 0.0] ## works for end effector facing down and cluster right above franka head
		self.qf = [0.0, (-70*np.pi)/180, 0.0, (160*np.pi)/180, 0.0, (np.pi)/2, 0.0]   # works well for final config desired 
		self.qf_frwd = [0.0, (-70*np.pi)/180, 0.0, (160*np.pi)/180, 0.0, (-np.pi)/2, 0.0] 
		self.Tf = 0.4 # originally set to 0.3 --> chnage with task and with position of robot wrt cluster
		self.T0 = 0
		self.nDof = 7
		self.t1samples = 200
		self.t2samples = 200
		self.extra_cond = 2 # cam + goal
		self.nb_cond = 2 + self.extra_cond
		self.max_nb_cond = 10 
		self.time_normalised1 = np.linspace(self.T0, self.Tf, self.t1samples)
		self.t_cam2 = self.time_normalised1[-1]-0.2 # self.Tf - (self.nb_cond / self.max_nb_cond) 
		self.init_cov = np.eye(len(self.q0)) * 0.000001
		self.Rgrip = 0.1
		self.Rcluster = 0.3
		self.k = 0.002
		self.Xi = 0.002
		self.pot_power = 2
		self.Q_star = 0.01 # change later
		self.stem_ndiscrete = 400
		self.constraints = {}
		self.goal = np.array([0.44999826901475964, 0.10000205479846114, 1.0599142533194694]) # init to straw81
		self.neighbours = []
		self.connections = []
		self.object_push_list = []
		self.traj_opt = traj_opt.trajectory_optimization(goal = self.goal,const =self.constraints, tcam2 = self.t_cam2, tsamples = self.t1samples, tf = self.Tf, push_obs = [])
		self.cost = []
		self.iter = []	
		self.ninter0 = 0
		self.ninter1 = 0
		self.tcam_samp = int((str(np.round(self.t_cam2*(self.t1samples)/self.Tf,0))).rstrip('0').rstrip('.') if '.' in (str(np.round(self.t_cam2*(self.t1samples)/self.Tf,0))) else (str(np.round(self.t_cam2*(self.t1samples)/self.Tf,0))))

		self.pubTrajD = rospy.Publisher('/arm_controller/command', JointTrajectory, queue_size=10)
		#self.subJState  = rospy.Subscriber("/joint_states", JointState, callback=self.RobotStateCallback)
		self.subSPose  = rospy.Subscriber("/straws_states", PoseArray, callback=self.ObjCallback)
		self.connections_debug = False
		self.plot_connections = False
		self.animation = True
		self.plot_cost = True
		self.plot_learntTraj = True
		self.plot_cond_traj = True
		self.plot_demos = False 
		self.plot_joint_cond_promp = False



	def RobotStateCallback(self,msg):
		name = msg.name
		print('msg state=', msg)
		self.q0 = msg.position[2:]
		print('q0=', self.q0)
		self.qdot0 = msg.velocity[2:]
		if (len(self.q0) != 0):
			self.subJState.unregister()



	def ObjCallback(self,msg):
		cluster = msg.poses
		cluster_frame = msg.header.frame_id
		self.goal = np.array([cluster[0].position.x, cluster[1].position.y, cluster[2].position.z]) 
		self.neighbours = [np.array([cluster[1].position.x, cluster[1].position.y, cluster[1].position.z]), np.array([cluster[2].position.x, cluster[2].position.y, cluster[2].position.z])]
		print(self.neighbours)

		if ((cluster_frame) and len(self.neighbours) != 0):
			print('started unregistering')
			self.subSPose.unregister()



	def cluster_specifications(self,): 
		#straw81 = np.array([-0.04998666399793127, 0.10000034078139142, 1.059910236708741, 4.516664479699469e-06])
		#straw82 = np.array([-0.04998839523886116, 0.10000034078139142, 0.9239243388589038, 0.3136100853490663])
		#straw83 = np.array([-0.04998755320876535, 0.10000034078139142, 0.9986486172601952, -0.33023169599299057])
		straw81 = np.array([0.44999826901475964, 0.10000205479846114, 1.0599142533194694, 4.244566712753927e-06])
		straw82 = np.array([0.44999851284989234, 0.0961539977781866, 0.9240894286207944, 0.3133357533444485]) # y has changed wrt sim
		straw83 = np.array([0.4499984064530141, 0.0978313002183852, 0.9985176875440531, -0.32215970186415416]) # y has changed wrt sim
		bunch_xyz = []
		bunch_col = []
		bunch_orient = []

		goal8c = True
		goal8_n1c = False
		goal8_n2c = False

		# orientation: rotation about y-axis of camera2 (mobile wrt arm base) , or about z-axis of camera1 (fixed wrt arm base) 
		# the following are preset orientation , call form.get_orientation when importing pcls
		config = 8
		theta8 = 0 

		goal8o = np.array([0,0,1])  # important goal orientation definition
		goal8_n1o = np.array([0,0,1]) 
		goal8_n2o = np.array([0,0,1])


		g8_n1_so = (np.pi)/10 # was (np.pi)/20 for matching sim  
		g8_n2_so = -(np.pi)/10


		#bunch_xyz.append(self.goal)
		#bunch_xyz.append(self.neighbours[0]) 
		#bunch_xyz.append(self.neighbours[1])
		# override the above 3 commented lines, until you solve the PoseArray() poses.append() in state_sub.py.. cz so far we are eliminating the last appended, and replaces it with the new one, so all elements get the same height
		bunch_xyz.append(straw81[0:3])
		bunch_xyz.append(straw82[0:3]) 
		bunch_xyz.append(straw83[0:3])

		bunch_col.append(goal8c)
		bunch_col.append(goal8_n1c)
		bunch_col.append(goal8_n2c)
		bunch_orient.append(goal8o)
		bunch_orient.append(goal8_n1o)
		bunch_orient.append(goal8_n2o)
		spherical_angles = [[g8_n1_so, 0], [g8_n2_so, 0]]
		Lstems = [0.28, 0.2, 0.13]

		return bunch_xyz, bunch_col, bunch_orient, spherical_angles, Lstems, config



	def jointTrajectoryCommand(self,traj, t=1):
		jt = JointTrajectory()
		jt.header.stamp = rospy.Time.now()
		jt.header.frame_id = "panda_arm"
		jt.joint_names.append("panda_arm_joint1")
		jt.joint_names.append("panda_arm_joint2")
		jt.joint_names.append("panda_arm_joint3")
		jt.joint_names.append("panda_arm_joint4")
		jt.joint_names.append("panda_arm_joint5")
		jt.joint_names.append("panda_arm_joint6")
		jt.joint_names.append("panda_arm_joint7")
		J1 = traj[:, 0]
		J2 = traj[:, 1]
		J3 = traj[:, 2]
		J4 = traj[:, 3]
		J5 = traj[:, 4]
		J6 = traj[:, 5]
		J7 = traj[:, 6]

		n = len(J1)
		dt = np.linspace(float(t)/n, t+float(t)/n, n) #added float before t to avoid future division

		for i in range (n):
			p = JointTrajectoryPoint()
			p.positions.append(J1[i])
			p.positions.append(J2[i])
			p.positions.append(J3[i])
			p.positions.append(J4[i])
			p.positions.append(J5[i])
			p.positions.append(J6[i])
			p.positions.append(J7[i])

			p.time_from_start = rospy.Duration.from_sec(dt[i])  # time_from_start is the point in time at which that TrajectoryPoint should be executed.

			jt.points.append(p)
		self.pubTrajD.publish(jt)
		#time.sleep(1)
		del p.positions[:]
		del jt.points[:]



	def load_demos(self,): 
		# Refer to simple_example.py in folder promp/examples/python_promp/simple_example.py, for comments on functions (simple_example.py and franka_promp.py are similar scripts)
		with open('/home/sariah/intProMP_franka/src/TrajOpt_UoL/promp_trajopt/traj_opt/src/100demos.npz', 'r') as f:
		    data = np.load(f, allow_pickle=True)
		    self.Q = data['Q']#[:97]
		    self.time = data['time']#[:97] # demo 98 has less than 30 samples
		Q_row = self.Q.shape


		################################################
		# To plot demonstrated end-eff trajectories
		if self.plot_demos == True:
			fig = plt.figure()
			ax = fig.add_subplot(111, projection='3d')
			for i in range(len(self.Q)):
			    endEffTraj = franka_kin.fwd_kin_trajectory(self.Q[i])
			    ax.scatter(endEffTraj[:,0], endEffTraj[:,1], endEffTraj[:,2], c='b', marker='.')
			plt.title('EndEff')
			plt.show()

		return self.Q, self.time
		######################################
		# To plot demonstrated trajectories Vs time

		# for plotDoF in range(7):
		#     plt.figure()
		#     for i in range(len(Q)):
		#         plt.plot(time[i] - time[i][0], Q[i][:, plotDoF])

		#     plt.title('DoF {}'.format(plotDoF))
		# plt.xlabel('time')
		# plt.title('demonstrations')
 
		############################################


	def promp_generator(self, IC, goal):

		phaseGenerator = phase.LinearPhaseGenerator()
		# best to change design parameters (basis nb, width, and num basis outside) choice with nb of neighbours
		# First chosen combiantion was: num basis=4, width = 5, outside =1) # if IC (q0) not straight up, and so choose Tf = 0.4 and t_cam = Tf -0.2
		# Later chosen combination was: num basis=5, width = 2, outside =3)  # if IC (q0) is straight up, and so choose Tf = 0.8 and t_cam = Tf -0.4
		# Later chosen combination was: num basis=5, width = 2, outside =5)
		basisGenerator1 = basis.NormalizedRBFBasisGenerator(phaseGenerator, numBasis=4, duration=self.Tf, basisBandWidthFactor=5, # check duration
		                                                   numBasisOutside=1) 


	
		proMP1 = promps.ProMP(basisGenerator1, phaseGenerator, self.nDof)
		plotDof = 2

		################################################################
		# Conditioning in JointSpace

		desiredTheta = np.array([0.5, 0.7, 0.5, 0.2, 0.6, 0.8, 0.1])
		desiredVar = np.eye(len(desiredTheta)) * 0.0001
		meanTraj, covTraj = proMP1.getMeanAndCovarianceTrajectory(self.time_normalised1)
		newProMP1 = proMP1.jointSpaceConditioning(self.Tf, desiredTheta=desiredTheta, desiredVar=desiredVar)
		traj = proMP1.getTrajectorySamples(self.time_normalised1, 1)
		print('traj =', traj.shape)

		#plt.figure()
		#plt.plot(time_normalised, trajectories[:, plotDof, :])
		#plt.xlabel('time')
		#plt.title('Joint-Space conditioning for joint 2')

		if self.plot_joint_cond_promp == True:
			proMP1.plotProMP(self.time_normalised1, [3, 4]) # refer to plotter.py, it plots means ans 2*std filled curves of newpromp, indices = [3, 4] refer to joints to plot


		##################################################
		# Conditioning in Task Space

		learnedProMP1 = promps.ProMP(basisGenerator1, phaseGenerator, self.nDof) # regression model initialization
		learner1 = promps.MAPWeightLearner(learnedProMP1) # weights learning model
		#print('q', Q[:][:120])
		learner1.learnFromData(self.Q, self.time) # get weights
		learned_promp1 = learnedProMP1.getTrajectorySamples(self.time_normalised1, 1)
		ttrajectories_learned = franka_kin.fwd_kin_trajectory(learned_promp1)
		mu_theta, sig_theta = learnedProMP1.getMeanAndCovarianceTrajectory(np.array([self.Tf])) # get mean cov of the learnt promp in joint space at time T = 1s
		#print('mu_theta=', np.squeeze(mu_theta))
		#print('sig_theta=', np.squeeze(sig_theta))
		sig_theta = np.squeeze(sig_theta)
		mu_x = goal # desired mean ee pose/position at T = 1s, old= [0.6, 0.5, 0.8]
		sig_x = np.eye(3) * 0.00000001 # desired cov of ee pose/position at T = 1s
		q_home = self.q0 #[0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.3]
		T_desired, tmp = franka_kin.fwd_kin(self.qf)
		mu_ang_euler_des = tf_tran.euler_from_matrix(T_desired, 'szyz')
		sig_euler = np.eye(3) * 0.0001
		print('starting IK')
		post_mean_tf, post_cov = franka_kin.inv_kin_ash_pose(np.squeeze(mu_theta), sig_theta, mu_x, sig_x, mu_ang_euler_des, sig_euler) # output is in joint space, desired mean and cov
		#print('post_mean =',post_mean_Ash.shape) 7x1
		#print('post_cov =',post_cov.shape) 7x7
		print('finishing IK')
		newProMP0 = learnedProMP1.jointSpaceConditioning(self.T0, desiredTheta= q_home, desiredVar=self.init_cov)
		newProMP1 = newProMP0.jointSpaceConditioning(self.Tf, desiredTheta= post_mean_tf, desiredVar=post_cov)
		trajectories_task1_conditioned = newProMP1.getTrajectorySamples(self.time_normalised1, 1)
		ttrajectories_task_conditioned = franka_kin.fwd_kin_trajectory(trajectories_task1_conditioned)

		# Get orientation of EE at T = 1s from demonstrations
		q_final = mu_theta
		q_final = np.squeeze(q_final)
		print('q_final=', q_final)
		T_final, tmp_final = franka_kin.fwd_kin(q_final)
		mu_ang_euler_final = tf_tran.euler_from_matrix(T_final, 'szyz')

		return newProMP1, ttrajectories_task_conditioned, mu_ang_euler_final, sig_euler, ttrajectories_learned


	def pushing_generator(self, GoalCond_promp, Goalpromp_Sampled, cond_pts, mu_ang_euler_final, sig_euler):
		# Get the min Z in the cluster 
		Goal = Goalpromp_Sampled[-1]
		minZ = Goal[2]
		for lowest in cond_pts:
			if lowest[2] < minZ:
				minZ = lowest[2]
		print('minZ=', minZ)
		coord_s1 = []
		print('cond_pts=', cond_pts)

		## random time vector, since we didn't collected data
		#tff1 = np.linspace(0,1, 152)
		#tff1 = np.repeat(np.array([tff1]), sdemo, axis = 0)

		#tff2 = np.linspace(0,1, 10)
		#tff2 = np.repeat(np.array([tff2]), sdemo, axis = 0)

		t0 = self.time_normalised1[0]
		tf = self.time_normalised1[-1]


		cond_pts_reduced = []

		## approach 1: to check if there is topset for the goal
		for i in range(0,len(cond_pts), 2):
			cond_pts_reduced.append(cond_pts[i])

		goal = np.array([Goal[0], Goal[1], Goal[2]+0.016])
		cluster_init = np.array([Goal[0], Goal[1], Goal[2]-0.016])


		start = []
		start.append(IC0)
		start.append(cluster_init)

		mu_x_IC = start[0]  

		# Conditioning at 1st goal point (same performance if cond at tf comes at end)
		print('cond_pts_reduced=', cond_pts_reduced)
		j = []

		if len(goal_update)!= 0:
			mu_x_tf = goal_update

		elif len(j) == 0 and len(goal_update)== 0: 
			print('i didnt get goal below nb')
			mu_x_tf = cluster_init
		else:
			inex = np.where(np.sum(np.abs(np.asarray(cond_pts) - np.asarray(cond_pts_reduced)[j[-1]]), axis = -1) == 0)
			inex = inex[-1][0]
			mu_x_tf = cond_pts[inex+1]


		#######################################################################################################################################

		# Conditioning at cluster bottom point :
		mu_theta1, sig_theta1 = GoalCond_promp.getMeanAndCovarianceTrajectory(np.array([self.t_cam2])) # get mean cov of the learnt promp in joint space at time T = 1s
		sig_theta1 = np.squeeze(sig_theta1)
		mu_x_t1g1 = [cluster_init[0], cluster_init[1], (minZ-0.1)]  # desired mean ee pose/position at T = 1s
		sig_x_t1g1 = np.eye(3) * 0.000002 # desired cov of ee pose/position at T = 1s
		post_mean_push, post_cov_push = franka_kin.inv_kin_ash_pose(np.squeeze(mu_theta1), sig_theta1, mu_x_t1g1, sig_x_t1g1, mu_ang_euler_final, sig_euler) # output is in joint space, desired mean and cov
		print('finishing IK')
		newProMP1 = GoalCond_promp.jointSpaceConditioning(self.t_cam2, desiredTheta=post_mean_push, desiredVar=post_cov_push)
		trajectories_task1_conditioned = newProMP1.getTrajectorySamples(self.time_normalised1, 1)

		#######################################################################################################################################


		## Pushing close neighbours
		mu_x_tfn = []
		if len(j) == 0 and len(goal_update)==0:
			for wpt in cond_pts:
				mu_x_tfn.append(wpt)
		elif len(j) == 0 and len(goal_update)!=0:
			for wpt in cond_pts:
				mu_x_tfn.append(wpt)
			mu_x_tfn.append(cluster_init)
		else:
			mu_x_tfn.append(cluster_init)
			cond_pts_sub = np.delete(np.asarray(cond_pts),-1, axis = 0) # to change the -1 with a systematic code
			print('cond_pts_sub=', cond_pts_sub)
			for el in cond_pts_sub:
				mu_x_tfn.append(el)

			
		t_discrete = np.linspace(self.t_cam2, tf, len(mu_x_tfn)+1,endpoint=False) # len(mu_x_tfn)
		print('t_discrete=', t_discrete)
		print('mu_x_tfn=', len(mu_x_tfn))
		t_effec = []
		mean_push = []
		cov_push = []

		traj_cond = []
		traj_cond.append(newProMP1)

		for k in range(1,len(mu_x_tfn)+1): # range(0,len(mu_x_tfn))
			## Nonlinear scale of t_discrete : exponential one
			tn = t_discrete[k]
			if (k % 2) != 0:
				tn = t_discrete[k] - t_discrete[k] * 0.0 # Tune those for trajectory variation minimization, + t_discrete[k+1] * 0.1 only add it in simulation to increase time to reach 1st cond point
			elif k==(len(mu_x_tfn)):
				tn = t_discrete[k] - t_discrete[k] * 0.0 # not much diff if 0.0
			else:
				tn = t_discrete[k] - t_discrete[k] * 0.0 # not much diff if 0.0
			t_effec.append(tn)
			print('tn=', tn)
			mu_thetan, sig_thetan = traj_cond[k-1].getMeanAndCovarianceTrajectory(np.array([tn])) # get mean cov of the learnt promp in joint space at time T = 1s
			sig_thetan = np.squeeze(sig_thetan)
			mu_x_tn = mu_x_tfn[k-1] 
			#print('mu_x=',mu_x_tng1)
			sig_x_tn = np.eye(3) * 0.0000001 # desired cov of ee pose
			post_mean_push, post_cov_push = franka_kin.inv_kin_ash_pose(np.squeeze(mu_thetan), sig_thetan, mu_x_tn, sig_x_tn, mu_ang_euler_final, sig_euler) # output is in joint space, desired mean and cov
			mean_push.append(post_mean_push)
			cov_push.append(post_cov_push)
			newProMPn = traj_cond[k-1].jointSpaceConditioning(tn, desiredTheta=post_mean_push, desiredVar=post_cov_push)
			trajectories_taskn_conditioned = newProMPn.getTrajectorySamples(self.time_normalised1, 1)

			traj_cond.append(newProMPn)

		## Save data for testing
	  	jtrajectories_pushed = trajectories_taskn_conditioned
		print('traj pushed jt=', jtrajectories_pushed.shape) 
		ttrajectories_pushed = franka_kin.fwd_kin_trajectory(np.squeeze(jtrajectories_pushed))
		print('traj pushed task=', ttrajectories_pushed.shape) 

		with open('promp_cond.npz', 'w') as f:
			np.save(f, jtrajectories_pushed)
		#with open('promp_goal.csv', 'w') as f2:
		#	np.savetxt(f2, trajectories_task1_conditioned, delimiter=',', fmt='%f')
		#with open('intprompV2_cond.csv', 'w') as f3:
		#	np.savetxt(f3, jtrajectories_pushed, delimiter=',', fmt='%f')
		return jtrajectories_pushed, ttrajectories_pushed, cond_pts, t_effec, mu_x_t1g1, mean_push, cov_push



	def optCallback(self, xk,state):   # xk is  joints traj, here we calculate total costs based on traj of joints; for BFGS remove state arg, for trust_region include state arg 
        # xk = xk.reshape(len(xk) / 7, 7)
		print("xk size: {}\n".format(len(xk)))
		costs = self.traj_opt.calculate_total_cost(xk) # xk as traj wll be nxlen(body pts)
		costs = np.squeeze(costs)
		self.cost.append(costs)
		self.iter.append(len(self.traj_opt.optCurve))
		self.traj_opt.optCurve.append(xk) # xk is the result of each optimization problem iteration
		print("Iteration {}: {}\n".format(len(self.traj_opt.optCurve), costs))  # first {} refers to first element of format() and second {} refers to second element



	def nonlinear_constraints_sariah(self, theta):


		c1 = []
		c2 = []		

		
		init_traj = np.transpose(theta.reshape(7,len(theta)/7))
		init_traj = init_traj[self.tcam_samp:, :]
		r, c = init_traj.shape

		for spls in range(len(init_traj)):
			close_neighbor_ind = []
			close_neighbor = []
			q = init_traj[spls,:]
			indices = np.where(np.in1d(theta, q))[0]
			
			T, Tj = franka_kin.fwd_kin([theta[indices[0]], theta[indices[1]], theta[indices[2]], theta[indices[3]], theta[indices[4]], theta[indices[5]], theta[indices[6]]])
			pt1 = T[0:3,3]
			#print('pt1=', pt1)
			pt2 = self.constraints['goal']
			#dist_norm = np.norm(dist)
			dist = pt1 - pt2
			d_proj = np.absolute(self.traj_opt.table_unit.dot(dist))
			p1 = np.linspace(pt1, pt2, self.stem_ndiscrete, endpoint=True)
			

			inter_pts = p1 in np.asarray(self.connections)
			#if p1 has at equal or below level (z) a neighbor then consider the stem of the closest cond_pts, else intersection is 
			# search in cond_pts those that have z = or less to pt1[2]
			for idx, sub in enumerate(self.constraints['condpts']):
				if pt1[2] - sub[2] >= -0.01: # -0.001
					#print('I detected a lower neighbor')
					close_neighbor_ind.append(idx)
					close_neighbor.append(sub)
			#print('close neighbor=', close_neighbor)
			#close_neighbor_ind = np.where((np.asarray(pt1[2]) - np.asarray(self.constraints['condpts'])) >= 0)
			if len(close_neighbor_ind) == 0:
				stem_inter = 'False'
			else:
				# find ind of closest neighbor to ee

				#ind_closest = np.where(close_neighbor == np.amax(close_neighbor, axis=0))
				#closest_neighbor = self.constraints['condpts'][close_neighbor_ind[ind_closest[2]]]
				#closest_ind = close_neighbor_ind[ind_closest[2]]
				closest_neighbor = self.constraints['condpts'][close_neighbor_ind[0]]
				closest_ind = close_neighbor_ind[0]
				if len(close_neighbor_ind) > 1:
					for ind in range(1, len(close_neighbor_ind)):
						if self.constraints['condpts'][close_neighbor_ind[ind]][2] > closest_neighbor[2]:
							closest_neighbor = self.constraints['condpts'][close_neighbor_ind[ind]][2] 
							closest_ind = close_neighbor_ind[ind]
				#closest_connection = connections[closest_ind]
				#noisy_connection = 
				stem_inter = np.any(p1 in np.asarray(self.connections[closest_ind]))
				#print('stem inter with lower closest neighbor=', stem_inter)
			if stem_inter == 'False':
				stem_inter = 0
				self.ninter0 +=1 
				#print('bool0=',stem_inter)
			else:
				stem_inter = 1
				self.ninter1 +=1 
				#print('bool1=',stem_inter)
		
			joint0_var = init_traj[spls,0]
			joint1_var = init_traj[spls,1]
			joint2_var = init_traj[spls,2]
			joint3_var = init_traj[spls,3]
			joint4_var = init_traj[spls,4]
			joint5_var = init_traj[spls,5]
			joint6_var = init_traj[spls,6]
			joint_var = [joint0_var, joint1_var, joint2_var, joint3_var, joint4_var, joint5_var, joint6_var]
			#print('d_proj=', d_proj)
			#print('stem_inter=', stem_inter)
			#print('spls=', spls)

			c1.append(d_proj)
			c2.append(stem_inter)
		#print('bool1=',self.ninter1)
		#print('bool0=',self.ninter0)
		#print('c1=', len(c1))
		#print('c2=', len(c2))
		constr = np.concatenate((c1, c2), axis=0)
		#constr = [np.asarray(c1), np.asarray(c2)]
		#constr = np.asarray(constr).reshape(-1)
		return constr



	def intprompV2_opt_sariah(self, condpromp, goal, cond_pts, tcond, inclination, Lstems, mean, cov):
		self.constraints = {'goal': goal, 'condpts':cond_pts,'tcond':tcond, 'incl': inclination, 'lstems':Lstems }
		self.goal = goal
		print('goal=', self.goal)

		for elem in range(len(self.constraints['condpts'])):
			X, Y, Z = fm.plot_3dseg(self.constraints['condpts'][elem], self.constraints['incl'][elem], self.constraints['lstems'][elem])
			connection = np.linspace(self.constraints['condpts'][elem], np.array([X[1],Y[1],Z[1]]), self.stem_ndiscrete)
			self.connections.append(connection)
			#for ele in connection:
			#	connections.append(ele)
		#print('condpts=', self.constraints['condpts'])

		if self.constraints:
			for condpt in self.constraints['condpts']:
				self.object_push_list.append(condpt)
			self.object_push_list.append(self.goal)
			for conn in self.connections:
				conn_slice = conn[::int((self.stem_ndiscrete-1)/2)]
				print('conn slice=', conn_slice)
				for pix_sample in conn_slice:
					self.object_push_list.append(pix_sample)


		self.traj_opt = traj_opt.trajectory_optimization(goal = self.goal,const =self.constraints, tcam2 = self.t_cam2, tsamples = self.t1samples, tf = self.Tf, push_obs = self.object_push_list)

		init_traj = condpromp # 200x7x1
		print('init traj=',init_traj[0,:,:])
		#init_traj = np.squeeze(init_traj) # 200x7
		bi, ri , ci= init_traj.shape  # b: blocks = 100 (time), r = rows(7, dof) and c = columns (20, samples from the Gaussian distribution at each dof at an instant of time)
		print('traj dim is: {}, {}, {}'.format(bi, ri, ci))
		#print('ProMP traj is: {}'.format(init_traj))
	 
		itraj_size = len(init_traj) 
		print('init traj size: {}'.format(itraj_size))

		initial_joint_values = init_traj[0,:,:]
		desired_joint_values = init_traj[(itraj_size - 1),:,:]
		trajectoryFlat = init_traj.T.reshape(-1) # by transpose: we got 20x7x100, by reshape(-1) we merge the elements of the original array in one line
		print('trajectoryflat = ',trajectoryFlat[200])
		bf = trajectoryFlat.shape
		print('probabilistic_trajflat dim is: {}'.format(bf))

		traj_mean = np.mean(init_traj, axis=2)
		bm, rm = traj_mean.shape
		print('mean proba traj dim is: {}, {}'.format(bm, rm)) 
		obst_cost_grad_analytic = np.reshape(self.traj_opt.calculate_obstacle_cost_gradient(trajectoryFlat), -1)
		#print('obst_cost_grad_analytic {} \n'.format(obst_cost_grad_analytic))  # gradient cost of initialized traj 

		################### With Gradient ############################
	    # minimize total cost
	    # scipy.minimize(fun, x0, args=(), method='BFGS', jac=None, tol=None, callback=None, options={'gtol': 1e-05, 'norm': inf, 'eps': 1.4901161193847656e-08, 'maxiter': None, 'disp': False, 'return_all': False})
	    # BFGS solver uses a limited computer memory and it is a popular algorithm for parameter estimation in machine learning.
	    # disp : Set to True to print convergence messages.
	    # mixter :max nb of iterations
	    # gtol : Gradient norm must be less than gtol before successful termination.
		sub_traj = init_traj[self.tcam_samp:,:,:]
		b, r, c = sub_traj.shape


		c1_lb = []
		c1_ub = []
		c2_lb = []
		c2_ub = []
		bounds_up = []
		bounds_low = []

		if self.constraints:
			for dem in range(ci): # alternate between ci and c ; and bi and b
				for spls in range(bi):
					bounds_low.append([-2.89725, -1.76278, -2.89725, -3.07178, -2.89725, -0.01745, -2.89725])
					bounds_up.append([2.89725, 1.76278, 2.89725, 0.0698132, 2.89725, 3.75246, 2.89725])

		if self.constraints:
			print('Constraints are available')
			for dem in range(c):
				for spls in range(b):
					c1_lb.append(self.Rgrip)
					c2_lb.append(0)
					c1_ub.append(np.inf)#2.0*self.Rcluster
					c2_ub.append(0)
					#bounds_low.append([-80*(np.pi)/180,-80*(np.pi)/180,-80*(np.pi)/180,-80*(np.pi)/180,-80*(np.pi)/180,-80*(np.pi)/180,-80*(np.pi)/180])
					#bounds_up.append([80*(np.pi)/180, 80*(np.pi)/180, 80*(np.pi)/180, 80*(np.pi)/180, 80*(np.pi)/180, 80*(np.pi)/180, 80*(np.pi)/180])
			
			#lb = [np.asarray(c1_lb), np.asarray(c2_lb)]
			#ub = [np.asarray(c1_ub), np.asarray(c2_ub)]
			#lb = np.asarray(lb).reshape(-1)
			#ub = np.asarray(ub).reshape(-1)
			lb = np.concatenate((c1_lb, c2_lb), axis=0)
			ub = np.concatenate((c1_ub, c2_ub), axis=0)

			print('ub shape=', np.asarray(ub).shape)
			print('lb shape=', np.asarray(lb).shape)
			up_bounds = np.asarray(bounds_up).T.reshape(-1)
			low_bounds = np.asarray(bounds_low).T.reshape(-1)
			
			bounds = Bounds(low_bounds, up_bounds)
			print('finish appending')

			constr = lambda theta: self.nonlinear_constraints_sariah(theta)											
	
			nLC = NonlinearConstraint(constr, lb, ub)
			print('finished formulating the NL constraints')
			#print('func=', init_traj[spls,:,dem].T.reshape(-1))
			#optimized_trajectory = opt.minimize(self.traj_opt.calculate_total_cost, trajectoryFlat, method='trust-constr', jac=self.traj_opt.cost_gradient_analytic, hess=opt.SR1(), constraints = nLC, callback=self.optCallback, options={'maxiter': 3, 'disp': True})
			

			optimized_trajectory = opt.minimize(self.traj_opt.calculate_total_cost, trajectoryFlat, method='SLSQP', jac=self.traj_opt.cost_gradient_analytic, constraints = [eq_cons, ineq_cons], callback = self.optCallback, options={'ftol': 1e-9, 'disp': True})  # or try SLSQP, options={'xtol': 1, 'gtol': 1, 'maxiter': 10, 'disp': True}
			#optimized_trajectory = opt.minimize(self.traj_opt.calculate_total_cost, trajectoryFlat, method='BFGS', jac=self.traj_opt.cost_gradient_analytic, callback=self.optCallback, options={'gtol': 2e-1, 'maxiter': 20, 'disp': True})
			print('finished inner optimization')

		print('passed the optimization, opt traj=', optimized_trajectory)
		optimized_trajectory = np.transpose(optimized_trajectory.x.reshape((7, len(optimized_trajectory.x) / 7)))
		##optimized_trajectory = np.insert(optimized_trajectory, 0, initial_joint_values, axis=0)
		##optimized_trajectory = np.insert(optimized_trajectory, len(optimized_trajectory), desired_joint_values, axis=0)
		optimized_trajectory[0] = np.squeeze(initial_joint_values)


		optimized_trajectory[-1] = np.squeeze(desired_joint_values)
		with open('traj_init_opt.npz', 'w') as f:
			np.save(f, [np.squeeze(init_traj), optimized_trajectory])


		with open('traj_opt.csv', 'w') as f4:
			np.savetxt(f4, optimized_trajectory, delimiter=',', fmt='%f')
		print('result of optimized trajectory {}\n'.format(optimized_trajectory))

		#Writer = animation.writers['ffmpeg']
		#writer = Writer(fps=15, metadata=dict(artist='Me'), bitrate=1800)
		#fig = plt.figure()
		#ani = FuncAnimation(fig, self.traj_opt.animation_sariah, frames=None, fargs=(np.squeeze(init_traj), goal, cond_pts, inclination, Lstems, fig), blit=False, save_count=100)
		#ani.save('push_opt.mp4', writer=writer)
		if self.plot_cost == True:
			fig2 = plt.figure()
			plt.plot(np.asarray(self.iter),np.asarray(self.cost))
			plt.xlabel('Iteration')
			plt.ylabel('Cost')
			plt.show()
		## Only if push is considered
		if self.plot_connections == True:
			self.plot_connections(self.connections, self.constraints['condpts'])
		if self.animation == True:
			self.traj_opt.animation(optimized_trajectory, np.squeeze(init_traj), goal, cond_pts, inclination, Lstems, self.connections)

		print('Finished plotting the optimized trajectory with Gradient passed to the minimization Algo')

		print('finished')	
		return optimized_trajectory


	def plot_connections(self, connections, cond_pts):

		fig = plt.figure()
		ax = fig.add_subplot(111, projection='3d')

		for Ip in range(len(cond_pts)):
			#if counting == 0:
			pt = cond_pts[Ip]
			ax.scatter(pt[0], pt[1], pt[2], s = 50, c='g', marker='o')
			#X, Y, Z = fm.plot_3dseg(pt, sph[Ip], lstems[Ip])
			#ax.plot3D(X, Y, Z, 'green')
		#Xg, Yg, Zg = fm.plot_3dseg(xf, [0,0], lstems[-1])
		#ax.plot3D(Xg, Yg, Zg, 'green')
		for conn0 in connections:
			for conn1 in conn0:
				ax.scatter(conn1[0], conn1[1], conn1[2], s = 2, c='r', marker='o')
		        plt.pause(.001)

		ax.set_xlabel('X')
		ax.set_ylabel('Y')
		ax.set_zlabel('Z')
		#plt.title('ProMP Conditioning at selected unripe pushable obstacles:\nCase of fruits')
		plt.show()


	def	plot_learnt_traj(self,traj, cond_pts, x0, xf):
		fig = plt.figure()
		ax = fig.add_subplot(111, projection='3d')
		ax.scatter(traj[:,0], traj[:,1], traj[:,2], c='b', marker='.')
		ax.scatter(x0[0], x0[1], x0[2], s = 100, c='y', marker='o')
		ax.scatter(xf[0], xf[1], xf[2], s = 100, c='r', marker='o')
		#counting = 0
		for Ip in range(0,len(cond_pts),2):
			print('len condpts=', len(cond_pts))
			#if counting == 0:
			pt = cond_pts[Ip]
			ax.scatter(pt[0], pt[1], pt[2], s = 50, c='g', marker='o')

		for obs in self.traj_opt.object_list_agri:
			## IF bar Obs
			#xpos, ypos, zpos, dx, dy, dz = fm.plot_3dbar(t=2, l=10, bins=1, x_center=obs[2], y_center=obs[1], xobs = obs[0], elev_factor=0.2)
			#ax.bar3d(zpos, xpos, ypos, dz, dx, dy, color='k', zsort='average')
    		#black_proxy = plt.Rectangle((0, 0), 1, 1, fc="k")
			## IF SPherical Obs
			x, y, z, alpha = fm.plot_sphere(obs[0:3], obs[3:])
			sphere = ax.plot_surface(x, y, z, color='b', alpha=alpha)
	        plt.pause(.001)

		ax.set_xlabel('X')
		ax.set_ylabel('Y')
		ax.set_zlabel('Z')
		#plt.title('ProMP Conditioning at selected unripe pushable obstacles:\nCase of fruits')
		plt.show()   


	def plot_traj(self, traj, cond_pts, x0, xf, sph, lstems, cam =None):

		fig = plt.figure()
		ax = fig.add_subplot(111, projection='3d')
		ax.scatter(traj[:,0], traj[:,1], traj[:,2], c='b', marker='.')
		ax.scatter(x0[0], x0[1], x0[2], s = 100, c='y', marker='o')
		ax.scatter(xf[0], xf[1], xf[2], s = 100, c='r', marker='o')
		if cam != None:
			ax.scatter(cam[0], cam[1], cam[2], s = 100, c='c', marker='o')
		#counting = 0
		for Ip in range(len(cond_pts)):
			#if counting == 0:
			pt = cond_pts[Ip]
			ax.scatter(pt[0], pt[1], pt[2], s = 50, c='g', marker='o')
			X, Y, Z = fm.plot_3dseg(pt, sph[Ip], lstems[Ip])
			ax.plot3D(X, Y, Z, 'green')
		Xg, Yg, Zg = fm.plot_3dseg(xf, [0,0], lstems[-1])
		ax.plot3D(Xg, Yg, Zg, 'green')
		for obs in self.traj_opt.object_list_agri:
			## IF Bar obstacle
			#xpos, ypos, zpos, dx, dy, dz = fm.plot_3dbar(t=2, l=10, bins=1, x_center=obs[2], y_center=obs[1], elev_factor=0.2)
			#ax.bar3d(zpos, xpos, ypos, dz, dx, dy, color='k', zsort='average')
    		#black_proxy = plt.Rectangle((0, 0), 1, 1, fc="k")
			## IF spherical obstacle
			x, y, z, alpha = fm.plot_sphere(obs[0:3], obs[3:])
			sphere = ax.plot_surface(x, y, z, color='b', alpha=alpha)
	        plt.pause(.001)

		ax.set_xlabel('X')
		ax.set_ylabel('Y')
		ax.set_zlabel('Z')
		#plt.title('ProMP Conditioning at selected unripe pushable obstacles:\nCase of fruits')
		plt.show()


	def plot_connections_verify(self,condpts, spherical, Lstems):
		constraints = {'condpts':condpts, 'incl': spherical, 'lstems':Lstems }

		connections = []
		for elem in range(len(constraints['condpts'])):
			#print(len(constraints['condpts']))
			connection = np.linspace(constraints['condpts'][elem], constraints['condpts'][elem]+constraints['lstems'][elem]*np.array([0,0,np.cos(constraints['incl'][elem][0])]), self.stem_ndiscrete)
			#print('connection shape=', connection.shape)
			connections.append(connection)

		fig = plt.figure()
		ax = fig.add_subplot(111, projection='3d')

		for Ip in range(len(cond_pts)):
			pt = cond_pts[Ip]
			ax.scatter(pt[0], pt[1], pt[2], s = 50, c='g', marker='o')

		for conn0 in connections:
			for conn1 in conn0:
				#print('conn1=', conn1)
				ax.scatter(conn1[0], conn1[1], conn1[2], s = 2, c='r', marker='o')
	        	plt.pause(.001)

		ax.set_xlabel('X')
		ax.set_ylabel('Y')
		ax.set_zlabel('Z')
		plt.show()


	def cluster_request(self,):
		bunch_xyz = []
		bunch_col = []
		bunch_orient = []

		# Collect point clouds
		X_goal7 = np.array([0, 0.5, 0.46-0.13])
		X_goal8 = np.array([0, 0.5, 0.46-0.13]) 
		X_goal9 = np.array([-0.08+0.2*np.sin((np.pi)/10), 0.5, 0.46-0.2*np.cos((np.pi)/10)])   

		# neighbors of X_goal1
		goal7_n1 = np.array([0.1-0.23*np.sin((np.pi)/6), X_goal7[1], 0.46-0.23*np.cos((np.pi)/6)])  	

		# neighbors of X_goal2
		goal8_n1 = np.array([0.1-0.28*np.sin((np.pi)/9), X_goal8[1], 0.46-0.28*np.cos((np.pi)/6)]) 
		goal8_n2 = np.array([-0.09+0.2*np.sin((np.pi)/7), X_goal8[1], 0.46-0.2*np.cos((np.pi)/8)])  

		# neighbors of X_goal3
		goal9_n1 = np.array([0.08-0.28*np.sin((np.pi)/8), 0.5, 0.46-0.28*np.cos((np.pi)/8)])  
		goal9_n2 = np.array([0, 0.5, 0.46-0.13])


		# colors
		X_goal7c = True #  is a mature fruit, red
		X_goal8c = True
		X_goal9c = True
		goal7_n1c = False
		goal8_n1c = False
		goal8_n2c = False
		goal9_n1c = False
		goal9_n2c = False


		# orientation: rotation about y-axis of camera2 (mobile wrt arm base) , or about z-axis of camera1 (fixed wrt arm base) 
		# the following are preset orientation , call form.get_orientation when importing pcls
		theta7 = 0 
		theta7n1 = (np.pi)/6 
		theta8 = 0 
		theta9 = (np.pi)/10
		#X_goal7o = np.array([0,0,1]) # important goal orientation definition
		X_goal7o = np.array([0,0,1]) 
		X_goal8o = np.array([0,0,1])  # important goal orientation definition
		X_goal9o = np.cos(theta9)* np.array([0,0,1])  # important goal orientation definition
		goal7_n1o = np.cos(theta7n1)* np.array([0,0,1])
		goal8_n1o = np.array([0,0,1]) 
		goal8_n2o = np.array([0,0,1])
		goal9_n1o = np.array([0,0,1]) 
		goal9_n2o = np.array([0,0,1])  


		g7_n1_so = (np.pi)/6 

		g8_n1_so = (np.pi)/20 
		g8_n2_so = -(np.pi)/20

		g9_n1_so = (np.pi)/8
		g9_n2_so = 0



		#print('Please enter which cluster configuration you want to simulate.')
		#print('case 7 is configuration where NF is below goal.')
		#print('case 8 is configuration where NF are below goal.')
		#print('case 9 is configuration where NF are @ below and above goal.')
		#conf = input()
		conf = 8
		if conf == 7:
			bunch_xyz.append(X_goal7) 
			bunch_xyz.append(goal7_n1)
			bunch_col.append(X_goal7c)
			bunch_col.append(goal7_n1c)
			bunch_orient.append(X_goal7o)
			bunch_orient.append(goal7_n1o)
			spherical_angles = [[g7_n1_so, 0]]
			Lstems = [0.23, 0.13]
		elif conf == 8:
			bunch_xyz.append(X_goal8)
			bunch_xyz.append(goal8_n1)
			bunch_xyz.append(goal8_n2)
			bunch_col.append(X_goal8c)
			bunch_col.append(goal8_n1c)
			bunch_col.append(goal8_n2c)
			bunch_orient.append(X_goal8o)
			bunch_orient.append(goal8_n1o)
			bunch_orient.append(goal8_n2o)
			spherical_angles = [[g8_n1_so, 0], [g8_n2_so, 0]]
			Lstems = [0.28, 0.2, 0.13]
		elif conf == 9:
			bunch_xyz.append(X_goal9) 
			bunch_xyz.append(goal9_n1)
			bunch_xyz.append(goal9_n2)
			bunch_col.append(X_goal9c)
			bunch_col.append(goal9_n1c)
			bunch_col.append(goal9_n2c)
			bunch_orient.append(X_goal9o)
			bunch_orient.append(goal9_n1o)
			bunch_orient.append(goal9_n2o) 
			spherical_angles = [[g9_n1_so,0], [g9_n2_so,0]] 
			Lstems = [0.28, 0.13, 0.2]
		else:
			print('configuration requested doesnt exist')

		return bunch_xyz, bunch_col, bunch_orient, spherical_angles, Lstems, conf



def promp_plotter(time_normalised, trajectories_task_conditioned):
	plt.figure()
	plt.plot(time_normalised, trajectories_task_conditioned[:, plotDof, :]) # 10 samples in joint space for joint : plotDof
	plt.xlabel('time')
	plt.title('Task-Space conditioning for joint 2')

	##############################################
	# Plot of end-effector trajectories

	fig = plt.figure()
	ax = fig.add_subplot(111, projection='3d')
	for i in range(trajectories_task_conditioned.shape[2]): # shape[2] is time samples
	    endEffTraj = franka_kin.fwd_kin_trajectory(trajectories_task_conditioned[:, :, i])
	    ax.scatter(endEffTraj[:, 0], endEffTraj[:, 1], endEffTraj[:, 2], c='b', marker='.')
	plt.xlabel('X')
	plt.ylabel('Y')
	plt.title('EE trajectories after task space Conditioning')
	##############################################
	plt.show()
	print('Finished')



##################################################
def promp_saver(trajectories_task_conditioned):
	# To save the task conditioned trajectories for playing back on robot

	with open('traject_task_conditioned1.npz', 'w') as f:
	    np.save(f, trajectories_task_conditioned)


##################################################



if __name__ == "__main__":
	#initialization = promp_pub()
	#while (initialization.q0.shape[0] == 0):
	#	rospy.loginfo('init state subscriber didnt return yet')
	#	time.sleep(1)
	intprompV2_generator = promp_pub()
	while len(intprompV2_generator.q0) == 0:
		rospy.loginfo('I am still waiting to get robot init state')

	#while (len(intprompV2_generator.goal) == 0 and len(intprompV2_generator.neighbours) == 0):
	# 	rospy.loginfo('I am still waiting to get the goal')

	data, time = intprompV2_generator.load_demos()
	Q = data 
	bunch_xyz, bunch_col, bunch_orient, spherical, Lstems, conf = intprompV2_generator.cluster_specifications()  #or cluster_request() 
	IC0, cluster_init, cond_pts, min_wpts2, subset_Ncond, goal_update = franka_actions.PushOpt_planner(bunch_xyz, bunch_col, bunch_orient, conf)
	print('goal=', cluster_init)
	#################################################### Assure IC0
	#IC0 = fk_kin(intprompV2_generator.q0)
	###################################################
	print('condpts=', cond_pts)
	GoalCond_promp, Goalpromp_Sampled, mu_ang_euler_final, sig_euler, ttrajectories_learned = intprompV2_generator.promp_generator(IC0, cluster_init)
	if intprompV2_generator.plot_learntTraj == True:
		intprompV2_generator.plot_learnt_traj(Goalpromp_Sampled, cond_pts, IC0, cluster_init)   
	intprompV2_joint, intprompV2_task, cond_pts, tcond, x_cam, mean_jcondpts, cov_jcondpts = intprompV2_generator.pushing_generator(GoalCond_promp, Goalpromp_Sampled, min_wpts2, mu_ang_euler_final, sig_euler)

	if intprompV2_generator.connections_debug == True:
		intprompV2_generator.plot_connections_verify(cond_pts, spherical,Lstems)
	
	intprompV2_generator.plot_traj(intprompV2_task, cond_pts, IC0, cluster_init, spherical, Lstems, cam =x_cam)
	intprompV2_Opt = intprompV2_generator.intprompV2_opt_sariah(intprompV2_joint, cluster_init, cond_pts, tcond, spherical, Lstems, mean_jcondpts, cov_jcondpts)
	#intprompV2_generator.jointTrajectoryCommand(intprompV2_Opt, t=100)
