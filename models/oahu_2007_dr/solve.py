#!/usr/local/bin/python

import sys, os, time, traceback
sys.path.append(os.path.abspath(os.path.dirname(__file__)))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../switch')))

from pyomo.environ import *
from pyomo.opt import SolverFactory, SolverStatus, TerminationCondition

import switch_mod.utilities as utilities
from switch_mod.utilities import define_AbstractModel

import util
import demand_response

opt = SolverFactory("cplex", solver_io="nl")
# tell cplex to find an irreducible infeasible set (and report it)
#opt.options['iisfind'] = 1

# relax the integrality constraints, to allow commitment constraints to match up with 
# number of units available
opt.options['mipgap'] = 0.001

print "loading model..."

# tell pyomo to make all parameters mutable by default
# (I only need to change lz_demand_mw, but this is currently the only way to make
# any parameter mutable without changing the core code.)
# NOTE: this causes errors like "TypeError: unhashable type: '_ParamData'" 
# when any parameters are used as indexes into a set (e.g., m.ts_scale_to_period[ts])
# This may be a pyomo bug, but it's hard to work around in the short term.
# Param.DefaultMutable = True

switch_model = define_AbstractModel(
    'switch_mod', 'fuel_cost', 'project.no_commit', #'project.unitcommit', 'project.unitcommit.discrete', 
    'demand_response', 'rps'
)
#switch_model.iis = Suffix(direction=Suffix.IMPORT)
switch_model.dual = Suffix(direction=Suffix.IMPORT)

switch_instance = switch_model.load_inputs(inputs_dir="inputs_rps")

results = None

def iterate():
    global switch_model, switch_instance, results
    # NOTE: some evil magic in demand_response 
    # turns on bid adjusting behavior if "adj_bad_bids" is in the tag
    # or bid dropping behavior if "drop_bad_bids" is in the tag
    tag = 'accel_rps_fixed_ce_200'
    for i in range(200):
        # solve the model repeatedly, iterating with a new demand function
        solve_once()

        if i <= 24 or i % 5 == 0:
            # save time by only writing results every 5 iterations
            write_results(tag=tag+'_'+str(i))
        
        #import pdb; pdb.set_trace()
        
        print "attaching new demand bid to model"
        demand_response.update_demand(switch_instance, tag)
        switch_instance.preprocess()

def write_results(tag=None):
    if tag is not None:
        t = "_"+str(tag)
    else:
        t = ""
    # write out results
    util.write_table(switch_instance, switch_instance.TIMEPOINTS,
        output_file=os.path.join("outputs", "dispatch{t}.txt".format(t=t)), 
        headings=("timepoint_label",)+tuple(switch_instance.PROJECTS),
        values=lambda m, t: (m.tp_timestamp[t],) + tuple(
            m.DispatchProj[p, t] if (p, t) in m.PROJ_DISPATCH_POINTS else 0.0 
            for p in m.PROJECTS
        )
    )
    util.write_table(switch_instance, switch_instance.TIMEPOINTS, 
        output_file=os.path.join("outputs", "load_balance{t}.txt".format(t=t)), 
        headings=
            ("timepoint_label",)
            +tuple(switch_instance.LZ_Energy_Balance_components)
            +("marginal_cost",),
        values=lambda m, t: 
            (m.tp_timestamp[t],) 
            +tuple(sum(getattr(m, component)[lz, t] for lz in m.LOAD_ZONES)
                    for component in m.LZ_Energy_Balance_components)
            +(sum(m.dual[m.Energy_Balance[lz, t]]/m.bring_timepoint_costs_to_base_year[t] for lz in m.LOAD_ZONES)
                /len(m.LOAD_ZONES),)
    )

    # Prepare tables of output data once, indexed by load_zone and timepoint,
    # so they can be spooled out row-by-row during the write_table operation. 
    # Otherwise, there's a lot of redundant list scanning and filtering.
    # +tuple(
    #     sum(
    #         m.DispatchProj[p, t]
    #         for p, t_ in m.PROJ_DISPATCH_POINTS
    #         if t_ == t and p in m.FUEL_BASED_PROJECTS and m.proj_fuel[p] == f
    #     )
    #     for f in m.FUELS
    # )
    # +tuple(
    #     sum(
    #         m.DispatchProj[p, t]
    #         for p, t_ in m.PROJ_DISPATCH_POINTS
    #         if t_ == t and p in m.NON_FUEL_BASED_PROJECTS and m.proj_non_fuel_energy_source[p] == s
    #     )
    #     for s in m.NON_FUEL_ENERGY_SOURCES
    # )
    # +tuple(
    #     sum(
    #         m.DispatchUpperLimit[p, t] - m.DispatchProj[p, t]
    #         for p, t_ in m.PROJ_DISPATCH_POINTS
    #         if t_ == t and p in m.NON_FUEL_BASED_PROJECTS and m.proj_non_fuel_energy_source[p] == s
    #     )
    #     for s in m.NON_FUEL_ENERGY_SOURCES
    # )
    
    util.write_table(
        switch_instance, switch_instance.LOAD_ZONES, switch_instance.TIMEPOINTS, 
        output_file=os.path.join("outputs", "energy_sources{t}.txt".format(t=t)), 
        headings=
            ("load_zone", "timepoint_label")
            +tuple(switch_instance.FUELS)
            +tuple(switch_instance.NON_FUEL_ENERGY_SOURCES)
            +tuple("curtail_"+s for s in switch_instance.NON_FUEL_ENERGY_SOURCES)
            +tuple(switch_instance.LZ_Energy_Balance_components)
            +("marginal_cost",),
        values=lambda m, z, t: 
            (z, m.tp_timestamp[t]) 
            +tuple(
                sum(
                    m.DispatchProj[p, t] 
                    for p, t_ in m.PROJ_DISPATCH_POINTS
                    if t_ == t and p in m.FUEL_BASED_PROJECTS and m.proj_fuel[p] == f
                )
                for f in m.FUELS
            )
            +tuple(
                sum(
                    m.DispatchProj[p, t] 
                    for p, t_ in m.PROJ_DISPATCH_POINTS
                    if t_ == t and p in m.NON_FUEL_BASED_PROJECTS and m.proj_non_fuel_energy_source[p] == s
                )
                for s in m.NON_FUEL_ENERGY_SOURCES
            )
            +tuple(
                sum(
                    m.DispatchUpperLimit[p, t] - m.DispatchProj[p, t] 
                    for p, t_ in m.PROJ_DISPATCH_POINTS
                    if t_ == t and p in m.NON_FUEL_BASED_PROJECTS and m.proj_non_fuel_energy_source[p] == s
                )
                for s in m.NON_FUEL_ENERGY_SOURCES
            )
            +tuple(getattr(m, component)[z, t] for component in m.LZ_Energy_Balance_components)
            +(m.dual[m.Energy_Balance[z, t]]/m.bring_timepoint_costs_to_base_year[t],)
    )
    util.write_table(switch_instance, switch_instance.LOAD_ZONES, switch_instance.TIMEPOINTS, 
        output_file=os.path.join("outputs", "marginal_cost{t}.txt".format(t=t)), 
        headings=("timepoint_label", "load_zone", "marginal_cost"),
        values=lambda m, lz, tp: 
            (m.tp_timestamp[tp], lz, m.dual[m.Energy_Balance[lz, tp]]/m.bring_timepoint_costs_to_base_year[tp])
    )
    # import pprint
    # b=[(pr, pe, value(m.BuildProj[pr, pe]), m.proj_gen_tech[pr], m.proj_overnight_cost[pr, pe]) for (pr, pe) in m.BuildProj if value(m.BuildProj[pr, pe]) > 0]
    # bt=set(x[3] for x in b) # technologies
    # pprint([(t, sum(x[2] for x in b if x[3]==t), sum(x[4] for x in b if x[3]==t)/sum(1.0 for x in b if x[3]==t)) for t in bt])

    
def solve_once():
    global switch_model, switch_instance, results

    print "solving model..."
    start = time.time()
    results = opt.solve(switch_instance, keepfiles=False, tee=True, 
        symbolic_solver_labels=True, suffixes=['dual'])
    print "Total time in solver: {t}s".format(t=time.time()-start)

    # results.write()
    if not switch_instance.load(results):
        raise RuntimeError("Unable to load solver results. Problem may be infeasible.")

    if results.solver.termination_condition == TerminationCondition.infeasible:
        print "Model was infeasible; Irreducible Infeasible Set (IIS) returned by solver:"
        #print "\n".join(c.cname() for c in switch_instance.iis)
        if util.interactive_session:
            print "Unsolved model is available as switch_instance."
        raise RuntimeError("Infeasible model")


    if util.interactive_session:
        print "Model solved successfully."
        print "Solved model is available as switch_instance."
    
    print "\n\n======================================================="
    print "Solved model"
    print "======================================================="
    print "Total cost: ${v:,.0f}".format(v=value(switch_instance.Minimize_System_Cost))
    print "marginal costs (first day):"
    print [switch_instance.dual[switch_instance.Energy_Balance[lz, tp]] \
                /switch_instance.bring_timepoint_costs_to_base_year[tp] 
            for lz in switch_instance.LOAD_ZONES
            for tp in switch_instance.TS_TPS[switch_instance.TIMESERIES[1]]]
    print "marginal costs (second day):"
    print [switch_instance.dual[switch_instance.Energy_Balance[lz, tp]] \
                /switch_instance.bring_timepoint_costs_to_base_year[tp] 
            for lz in switch_instance.LOAD_ZONES
            for tp in switch_instance.TS_TPS[switch_instance.TIMESERIES[2]]]
        
if __name__ == '__main__':
    # catch errors so the user can continue with a solved model
    try:
        iterate()
    except Exception, e:
        traceback.print_exc()
        print "ERROR:", e
    