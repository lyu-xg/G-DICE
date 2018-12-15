import numpy as np
from functools import partial
from multiprocessing import Pool
from .Domains import MultiPOMDP
from .Scripts import saveResults

# Run GDICE with controller(s) on an environment, given
# Inputs:
#   env: Gym-like environment to evaluate on
#   controller: A controller or list of controllers corresponding to agents in the environment
#   params: GDICEParams object
#   timeHorizon: Number of timesteps to evaluate to. If None, run each sample until episode is finished
#   parallel: Attempt to use python multiprocessing across samples. If not None, should be a Pool object
#   convergenceThreshold: If set, attempts to detect early convergence within a run and stop before all iterations are done
#   saveFrequency: How frequently to save results in the middle of a run (numIterations between saves)
def runGDICEOnEnvironment(env, controller, params, parallel=None, convergenceThreshold=0, saveFrequency=50):
    # Ensure controller matches environment
    assert env.action_space.n == controller.numActions
    assert env.observation_space.n == controller.numObservations

    # Reset controller
    controller.reset()
    timeHorizon = params.timeHorizon

    # Start variables
    bestActionProbs = None
    bestNodeTransitionProbs = None
    bestValue = np.NINF
    bestValueAtEachIteration = np.full(params.numIterations, np.nan, dtype=np.float64)
    bestStdDevAtEachIteration = np.full(params.numIterations, np.nan, dtype=np.float64)
    bestValueVariance = 0
    worstValueOfPreviousIteration = np.NINF
    allValues = np.zeros((params.numIterations, params.numSamples), dtype=np.float64)
    allStdDev = np.zeros((params.numIterations, params.numSamples), dtype=np.float64)
    estimatedConvergenceIteration = 0

    for iteration in range(params.numIterations):
        controllerChange = False  # Did the controller change this iteration?
        iterBestValue = np.NINF  # What is the most recently seen best controller value
        # For each node in controller, sample actions
        sampledActions = controller.sampleActionFromAllNodes(params.numSamples)  # numNodes*numSamples

        # For each node, observation in controller, sample next node
        sampledNodes = controller.sampleAllObservationTransitionsFromAllNodes(params.numSamples)  # numObs*numBeginNodes*numSamples

        # For each sampled action, evaluate in environment
        # For parallel, try single environment. For single core (or low memory), use MultiEnv
        if parallel is not None and isinstance(parallel, type(Pool)):
            envEvalFn = partial(evaluateSample, timeHorizon=timeHorizon, numSimulations=params.numSimulationsPerSample)
            values, stdDev = [(np.array(res[0]), np.array(res[1])) for res in parallel.starmap(envEvalFn, [(env, sampledActions[:,i], sampledNodes[:,:,i]) for i in range(params.numSamples)])]
        else:
            values, stdDev = evaluateSamplesMultiEnv(MultiPOMDP(env, params.numSamples), timeHorizon, params.numSimulationsPerSample, sampledActions, sampledNodes)

        # Save values
        allValues[iteration, :] = values
        allStdDev[iteration, :] = stdDev

        # Find N_b best policies
        bestSampleIndices = values.argsort()[-params.numBestSamples:]
        bestValues = values[bestSampleIndices]
        sortedStdDev = stdDev[bestSampleIndices]

        # Save best policy (if better than overall previous)
        if bestValue < bestValues[-1]:
            controllerChange = True
            bestValue = bestValues[-1]
            bestValueVariance = sortedStdDev[-1]
            bestActionProbs = sampledActions[:, bestSampleIndices[-1]]
            bestNodeTransitionProbs = sampledNodes[:, :, bestSampleIndices[-1]]

        # Throw away policies below value threshold (worst best value of previous iteration)
        keepIndices = np.where(bestValues >= worstValueOfPreviousIteration)[0]
        bestValues = bestValues[keepIndices]
        bestSampleIndices = bestSampleIndices[keepIndices]

        #If we're using a value threshold, also throw away iterations below that
        if params.valueThreshold is not None:
            keepIndices = np.where(bestValues >= params.valueThreshold)[0]
            bestValues = bestValues[keepIndices]
            bestSampleIndices = bestSampleIndices[keepIndices]


        # For each node, update using best samples
        controller.updateProbabilitiesFromSamples(sampledActions[:,bestSampleIndices], sampledNodes[:,:,bestSampleIndices], params.learningRate)
        print('After '+str(iteration+1) + ' iterations, best (discounted) value is ' + str(bestValue) + 'with standard deviation '+str(bestValueVariance))
        bestValueAtEachIteration[iteration] = bestValue
        bestStdDevAtEachIteration[iteration] = bestValueVariance
        # If the value stops improving, maybe we've converged?
        if iterBestValue < bestValue+convergenceThreshold:
            iterBestValue = bestValue
        else:
            iterBestValue = bestValue
            estimatedConvergenceIteration = iteration
            # if we're using a convergence threshold, can terminate early
            if convergenceThreshold and controllerChange:
                break

        # Save occasionally so we don't lose everything in a crash. Saves relative to working dir
        if saveFrequency and iteration % saveFrequency == 0:
            saveResults('', env.spec.id, params, (bestValue, bestValueVariance, bestActionProbs, bestNodeTransitionProbs,
                                               controller, estimatedConvergenceIteration, allValues, allStdDev, bestValueAtEachIteration, bestStdDevAtEachIteration))


    # Return best policy, best value, updated controller
    return bestValue, bestValueVariance, bestActionProbs, bestNodeTransitionProbs, controller, \
           estimatedConvergenceIteration, allValues, allStdDev, bestValueAtEachIteration, bestStdDevAtEachIteration


# Evaluate a single sample, starting from first node
# Inputs:
#   env: Environment in which to evaluate
#   timeHorizon: Time horizon over which to evaluate
#   actionTransitions: (numNodes,) int array of chosen actions for each node
#   nodeObservationTransitions: (numObs, numNodes) int array of chosen node transitions for obs
#  Output:
#    value: Discounted total return over timeHorizon (or until episode is done), averaged over all simulations
#    stdDev: Standard deviation of discounter total returns over all simulations
def evaluateSample(env, timeHorizon, numSimulations, actionTransitions, nodeObservationTransitions):
    gamma = env.discount if env.discount is not None else 1
    values = np.zeros(numSimulations, dtype=np.float64)
    for sim in numSimulations:
        env.reset()
        currentNodeIndex = 0
        currentTimestep = 0
        isDone = False
        value = 0.0
        while not isDone and currentTimestep < timeHorizon:
            obs, reward, isDone = env.step(actionTransitions[currentNodeIndex])
            currentNodeIndex = nodeObservationTransitions[obs, currentNodeIndex]
            value += reward * (gamma ** currentTimestep)
            currentTimestep += 1
        values[sim] = value
    return values.mean(), values.std()

# Evaluate multiple samples, starting from first node
# Inputs:
#   env: MultiEnv environment in which to evaluate
#   timeHorizon: Time horizon over which to evaluate
#   actionTransitions: (numNodes,numSamples) int array of chosen actions for each node
#   nodeObservationTransitions: (numObs, numNodes, numSamples) int array of chosen node transitions for obs
#  Output:
#    allSampleValues: Discounted total return over timeHorizon (or until episode is done), averaged over all simulations, for each sample (numSamples,)
#    stdDevs: Standard deviation of discounted total returns over all simulations, for each sample (numSamples,)
def evaluateSamplesMultiEnv(env, timeHorizon, numSimulations, actionTransitions, nodeObservationTransitions):
    assert isinstance(env, MultiPOMDP)
    gamma = env.discount if env.discount is not None else 1
    numSamples = actionTransitions.shape[-1]
    sampleIndices = np.arange(numSamples)
    allSampleValues = np.zeros((numSimulations, numSamples), dtype=np.float64)
    for sim in range(numSimulations):
        env.reset()
        currentNodes = np.zeros(numSamples, dtype=np.int32)
        currentTimestep = 0
        values = np.zeros(numSamples, dtype=np.float64)
        isDones = np.zeros(numSamples, dtype=bool)
        while not all(isDones) and currentTimestep < timeHorizon:
            obs, rewards, isDones = env.step(actionTransitions[currentNodes, sampleIndices])[:3]
            currentNodes = nodeObservationTransitions[obs, currentNodes, sampleIndices]
            values += rewards * (gamma ** currentTimestep)
            currentTimestep += 1
        allSampleValues[sim, :] = values
    return allSampleValues.mean(axis=0), allSampleValues.std(axis=0)