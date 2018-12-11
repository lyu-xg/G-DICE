from gym_pomdps import list_pomdps, POMDP
import gym
import numpy as np
import numpy.random as npr
from multiprocessing import Pool
from functools import partial
from inspect import isclass

class FiniteStateController(object):
    def __init__(self, numNodes, numActions, numObservations):
        self.numNodes = numNodes
        self.numActions = numActions
        self.numObservations = numObservations
        self.currentNode = None
        self.initActionNodeProbabilityTable()
        self.initObservationNodeTransitionProbabilityTable()

    # Probability of each action given being in a certain node
    def initActionNodeProbabilityTable(self):
        initialProbability = 1 / self.numActions
        self.actionProbabilities = np.full((self.numNodes, self.numActions), initialProbability)

    # Probability of transition from 1 node to second node given obsersvation
    def initObservationNodeTransitionProbabilityTable(self):
        initialProbability = 1 / self.numNodes
        self.nodeTransitionProbabilities = np.full((self.numNodes,
                                                    self.numNodes,
                                                    self.numObservations), initialProbability)

    # Set the current node of the controller
    def setNode(self, nodeIndex):
        self.currentNode = nodeIndex

    # Returns the index of the current node
    def getCurrentNode(self):
        return self.currentNode

    # Reset the controller to default probabilities
    def reset(self):
        self.setNode(None)
        self.initActionNodeProbabilityTable()
        self.initObservationNodeTransitionProbabilityTable()

    # Get an action using the current node according to probability. Can sample multiple actions
    def sampleAction(self, numSamples=1):
        return npr.choice(np.arange(self.numActions), size=numSamples, p=self.actionProbabilities[self.currentNode, :])

    # Get an action from all nodes according to probability. Can sample multiple actions
    # Outputs numNodes * numSamples
    def sampleActionFromAllNodes(self, numSamples=1):
        actionIndices = np.arange(self.numActions)
        return np.array([npr.choice(actionIndices, size=numSamples, p=self.actionProbabilities[nodeIndex,:])
                         for nodeIndex in range(self.numNodes)], dtype=np.int32)

    # Get the next node according to probability given current node and observation index
    # Can sample multiple transitions
    # DOES NOT set the current node
    def sampleObservationTransition(self, observationIndex, numSamples=1):
        return npr.choice(np.arange(self.numNodes), size=numSamples, p=self.nodeTransitionProbabilities[self.currentNode, :, observationIndex])

    # Get the next node for each node given observation index
    # Outputs numNodes * numSamples
    def sampleObservationTransitionFromAllNodes(self, observationIndex, numSamples=1):
        nodeIndices = np.arange(self.numNodes)
        return np.array([npr.choice(nodeIndices, size=numSamples, p=self.nodeTransitionProbabilities[nodeIndex, :, observationIndex])
                         for nodeIndex in range(self.numNodes)], dtype=np.int32)

    # Get the next node for all nodes for all observation indices
    # Outputs numObs * numNodes * numSamples
    def sampleAllObservationTransitionsFromAllNodes(self, numSamples=1):
        obsIndices = np.arange(self.numObservations)
        return np.array([self.sampleObservationTransitionFromAllNodes(obsIndex, numSamples)
                         for obsIndex in obsIndices], dtype=np.int32)

    def updateProbabilitiesFromSamples(self, actions, nodeObs, learningRate):
        if len(actions) == 0:  # No samples, no update
            return
        assert actions.shape[-1] == nodeObs.shape[-1]  # Same # samples
        if len(actions.shape) == 1:  # 1 sample
            weightPerSample = 1
            numSamples = 1
            actions = np.expand_dims(actions, axis=1)
            nodeObs = np.expand_dims(nodeObs, axis=2)
        else:
            weightPerSample = 1/actions.shape[-1]
            numSamples = actions.shape[-1]

        # Reduce
        self.actionProbabilities = self.actionProbabilities * (1-learningRate)
        self.nodeTransitionProbabilities = self.nodeTransitionProbabilities * (1-learningRate)
        nodeIndices = np.arange(0, self.numNodes, dtype=int)
        obsIndices = np.arange(0,self.numObservations, dtype=int)

        # Add samples factored by weight
        for sample in range(numSamples):
            self.actionProbabilities[nodeIndices, actions[:,sample]] += learningRate*weightPerSample
            #self.nodeTransitionProbabilities[nodeIndices, nodeObs[repObsIndices, nodeIndices, sample], obsIndices] += learningRate*weightPerSample
            for observation in range(nodeObs.shape[0]):
                for startNode in range(nodeObs.shape[1]):
                    self.nodeTransitionProbabilities[startNode, nodeObs[observation,startNode,sample], observation] += learningRate*weightPerSample





    # Update the probability of taking an action in a particular node
    # Can be used for multiple inputs if numNodeIndices = n, numActionIndices = m, and newProbability = n*m or a scalar
    def updateActionProbability(self, nodeIndex, actionIndex, newProbability):
        self.actionProbabilities[nodeIndex, actionIndex] = newProbability

    # Update the probability of transitioning from one node to a second given an observation
    def updateTransitionProbability(self, firstNodeIndex, secondNodeIndex, observationIndex, newProbability):
        self.nodeTransitionProbabilities[firstNodeIndex, secondNodeIndex, observationIndex] = newProbability

    # Get the probability vector for node(s)
    def getPolicy(self, nodeIndex):
        return self.actionProbabilities[np.array(nodeIndex, dtype=np.int32), :]

    # Get the current probability tables
    def save(self):
        return self.actionProbabilities, self.nodeTransitionProbabilities


# Evaluate controller(s) on an environment, given
# Inputs:
#   env: Gym-like environment to evaluate on
#   controller: A controller or list of controllers corresponding to agents in the environment
#   params: GDICEParams object
#   timeHorizon: Number of timesteps to evaluate to. If None, run each sample until episode is finished
#   parallel: Attempt to use python multiprocessing across samples. If not None, should be a Pool object

def evaluateFSCOnEnvironment(env, controller, params, timeHorizon=50, parallel=None):
    # Ensure controller matches environment
    assert env.action_space.n == controller.numActions
    assert env.observation_space.n == controller.numObservations

    # Reset controller
    controller.reset()

    # Get environment gamma
    gamma = env.discount

    # Start variables
    bestActionProbs = None
    bestNodeTransitionProbs = None
    bestValue = np.NINF
    worstValueOfPreviousIteration = np.NINF
    allValues = np.zeros((params.numIterations, params.numSamples))

    # Wrap the environment to sample multiple trajectories simultaneously
    multiEnv = MultiActionPOMDP(env, numTrajectories=params.numSamples)

    for iteration in range(params.numIterations):
        # For each node in controller, sample actions
        sampledActions = controller.sampleActionFromAllNodes(params.numSamples)  # numNodes*numSamples

        # For each node, observation in controller, sample next node
        sampledNodes = controller.sampleAllObservationTransitionsFromAllNodes(params.numSamples)  # numObs*numBeginNodes*numSamples

        # For each sampled action, evaluate in environment
        # For parallel, try single environment. For single core (or low memory), use MultiEnv
        if parallel is not None and isinstance(parallel, type(Pool)):
            env.reset()
            envEvalFn = partial(evaluateSample, env, timeHorizon)
            values = np.array(parallel.starmap(envEvalFn, [(sampledActions[:,i], sampledNodes[:,:,i]) for i in range(params.numSamples)]))
        else:
            multiEnv.reset()
            sampleIndices = np.arange(params.numSamples)
            currentNodes = np.zeros(params.numSamples, dtype=np.int32)
            currentTimestep = 0
            values = np.zeros(params.numSamples, dtype=np.float64)
            isDones = np.zeros(params.numSamples, dtype=bool)
            while not all(isDones) and currentTimestep < timeHorizon:
                obs, rewards, isDones = multiEnv.step(sampledActions[currentNodes, sampleIndices])[:3]
                currentNodes = sampledNodes[obs, currentNodes, sampleIndices]
                values += rewards * (gamma ** currentTimestep)
                currentTimestep += 1

        # Save values
        allValues[iteration, :] = values

        # Find N_b best policies
        bestSampleIndices = values.argsort()[-params.numBestSamples:]
        bestValues = values[bestSampleIndices]

        # Save best policy (if better than overall previous)
        if bestValue < bestValues[-1]:
            bestValue = bestValues[-1]
            bestActionProbs = sampledActions[:, bestSampleIndices[-1]]
            bestNodeTransitionProbs = sampledNodes[:, :, bestSampleIndices[-1]]

        # Throw away policies below value threshold (worst best value of previous iteration)
        keepIndices = np.where(bestValues >= worstValueOfPreviousIteration)[0]
        bestValues = bestValues[keepIndices]
        bestSampleIndices = bestSampleIndices[keepIndices]

        # For each node, update using best samples
        controller.updateProbabilitiesFromSamples(sampledActions[:,bestSampleIndices], sampledNodes[:,:,bestSampleIndices], params.learningRate)
        print('After '+str(iteration+1) + ' iterations, best (discounted) value is ' + str(bestValue))

    # Return best policy, best value, updated controller
    return bestValue, bestActionProbs, bestNodeTransitionProbs, controller


# Evaluate a single sample, starting from first node
# Inputs:
#   env: Environment in which to evaluate
#   timeHorizon: Time horizon over which to evaluate
#   actionTransitions: (numNodes,) int array of chosen actions for each node
#   nodeObservationTransitions: (numObs, numNodes) int array of chosen node transitions for obs
#  Output:
#    value: Discounted total return over timeHorizon (or until episode is done)
def evaluateSample(env, timeHorizon, actionTransitions, nodeObservationTransitions):
    gamma = env.discount
    currentNodeIndex = 0
    currentTimestep = 0
    isDone = False
    value = 0.0
    while not isDone and currentTimestep < timeHorizon:
        obs, reward, isDone = env.step(actionTransitions[currentNodeIndex])
        currentNodeIndex = nodeObservationTransitions[obs, currentNodeIndex]
        value += reward * (gamma ** currentTimestep)
        currentTimestep += 1
    return value



# GDICE parameter object
# Inputs:
#   numIterations: N_k number of iterations of GDICE to perform
#   numSamples: N_s number of samples to take for each iteration from each node
#   numBestSamples: N_b number of samples to keep from each set of samples
#   leareningRate: 0-1 alpha value, learning rate at which controller shifts probabilities
#   valueThreshold: If not None, ignore all samples with worse values, even if that means there aren't numBestSamples
class GDICEParams(object):
    def __init__(self, numIterations=30, numSamples=50, numBestSamples=5, learningRate=0.1, valueThreshold=None):
        self.numIterations = numIterations
        self.numSamples = numSamples
        self.numBestSamples = numBestSamples
        self.learningRate = learningRate
        self.valueThreshold = valueThreshold


# States, observations, rewards, actions, dones are now lists or np arrays
class MultiActionPOMDP(gym.Wrapper):

    def __init__(self, env, numTrajectories):
        assert isinstance(env, POMDP)
        super().__init__(env)
        self.numTrajectories = numTrajectories
        self.reset()

    def __getattr__(self, attr):
        return getattr(self.env, attr)

    def reset(self):
        if self.env.start is None:
            self.state = self.np_random.randint(
                self.state_space.n, size=self.numTrajectories)
        else:
            self.state = self.np_random.multinomial(
                1, self.env.start, size=self.numTrajectories).argmax(1)

    # Step given an nparray or list of actions
    # If actions is a scalar, applies to all
    def step(self, actions):
        # Scalar action given, apply to all
        if np.isscalar(actions):
            actions = np.full(self.numTrajectories, actions, dtype=np.int32)

        # Tuple of (action, index) given, step for one worker only
        if isinstance(actions, tuple) and len(actions) == 2:
            return self._stepForSingleWorker(int(actions[0]), int(actions[1]))

        # For each agent that is done, return nothing
        doneIndices = np.nonzero(self.state == -1)[0]
        notDoneIndices = np.nonzero(self.state != -1)[0]

        # Blank init
        newStates = np.zeros(self.numTrajectories, dtype=np.int32)
        obs = np.zeros(self.numTrajectories, dtype=np.int32)
        rewards = np.zeros(self.numTrajectories, dtype=np.float64)
        done = np.ones(self.numTrajectories, dtype=bool)

        # Reduced list based on which workers are done. If env is not episodic, this will still work
        validStates = self.state[notDoneIndices]
        validActions = actions[notDoneIndices]
        validNewStates = np.array([self.np_random.multinomial(1, p).argmax() for p in self.env.T[validStates, validActions]])
        validObs = np.array([self.np_random.multinomial(1, p).argmax() for p in self.env.O[validStates, validActions, validNewStates]])
        validRewards = np.array(self.env.R[validStates, validActions, validNewStates, validObs])
        if self.env.episodic:
            done[notDoneIndices] = self.env.D[self.state, actions]
        else:
            done *= False

        newStates[notDoneIndices], newStates[doneIndices] = validNewStates, -1
        obs[notDoneIndices], obs[doneIndices] = validObs, -1
        rewards[notDoneIndices], rewards[doneIndices] = validRewards, 0.0
        self.states = newStates

        return obs, rewards, done, {}

    # If multiprocessing, each worker will provide its trajectory index and desired action
    def _stepForSingleWorker(self, action, index):
        currState = self.state[index]

        # If this worker's episode is finished, return nothing
        if currState is None:
            return -1, 0.0, True, {}

        newState = self.np_random.multinomial(1, self.env.T[currState, action]).argmax()
        obs = self.np_random.multinomial(1, self.env.O[currState, action, newState]).argmax()
        reward = self.env.R[currState, action, newState, obs]
        if self.env.episodic:
            done = self.env.D[currState, action]
        else:
            done = False

        if done:
            self.state[index] = -1
        else:
            self.state[index] = newState

        return obs, reward, done, {}



if __name__ == "__main__":
    env = gym.make(list_pomdps()[1])  # POMDP-1d-episodic-v0
    controller = FiniteStateController(10, env.action_space.n, env.observation_space.n)
    testParams = GDICEParams()
    #pool = Pool()  # Use a pool for parallel processing. Max # threads
    evaluateFSCOnEnvironment(env, controller, testParams, timeHorizon=50, parallel=None)
