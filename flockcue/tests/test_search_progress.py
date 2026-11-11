"""Public-evidence search handoff boundaries; no provider or hidden task state."""
import copy
import socket
import pytest
import harness_skills
from flockcue.evidence.search_policy import PublicSearchPolicy
from flockcue.runtime.core import HarnessRuntime
from flockcue.runtime.search_progress import PublicSearchProgress
from test_harness_flight_state import FlightWorld, reject_without_effect
from test_harness_observed_target import TargetWorld, make as target_runtime, nearby, pure, target
from test_harness_shared_endpoint import make
from test_harness_terminal_progress import normalized_progress
from test_minimal_harness import finish

V8 = "search_progress_v8"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("Search-progress tests must not use network")
    monkeypatch.setattr(socket.socket, "connect", fail)
    monkeypatch.setattr(socket.socket, "connect_ex", fail)
    monkeypatch.setattr(socket, "create_connection", fail)


def read(gate, attempts, progress, trigger="periodic", **kwargs):
    return gate.observe(normalized_progress(progress), f"e{len(gate.reads)+1}",
        attempts=attempts, trigger=trigger, **kwargs)


def test_positive_plateau_requires_two_reads_and_does_not_claim_completion():
    g=PublicSearchProgress("s1")
    assert read(g,0,33,"entry")==read(g,10,33)=="continue"
    assert read(g,20,33)=="handoff"
    saved=g.snapshot()
    assert saved["infeasibility_proven"] is False and saved["mission_completion"]=="not_evaluated"
    saved["reads"].clear()
    assert len(g.reads)==3


def test_zero_progress_preserves_original_exploration_window():
    g=PublicSearchProgress("s1");read(g,0,0,"entry")
    assert all(read(g,i,0)=="continue" for i in range(10,81,10))
    assert g.stagnant_reads==0


def test_reach_confirmation_does_not_count_short_partial_window_as_stagnant():
    g=PublicSearchProgress("s1");read(g,0,0,"entry");read(g,10,0)
    read(g,13,33,"new_public_reach")
    assert read(g,20,33)=="continue" and g.stagnant_reads==0
    assert read(g,30,33)=="continue" and g.stagnant_reads==1
    read(g,34,66,"new_public_reach")
    assert read(g,40,66)=="continue" and g.stagnant_reads==0
    assert read(g,50,66)=="continue" and g.stagnant_reads==1
    assert read(g,60,66)=="handoff"


def test_new_target_pending_target_and_progress_increase_reset_before_handoff():
    g=PublicSearchProgress("s1");read(g,0,33,"entry");read(g,10,33)
    assert read(g,20,33,fixed_target_ids={"t1"})=="continue" and g.stagnant_reads==0
    assert read(g,30,33,fixed_target_ids={"t1"},pending_target_ids={"t1"})=="continue" and g.stagnant_reads==0
    assert read(g,40,66,fixed_target_ids={"t1"})=="continue" and g.stagnant_reads==0


@pytest.mark.parametrize("p,decision",[(95,"continue"),(95.01,"public_stop_condition"),(100,"public_stop_condition")])
def test_strict_public_threshold(p,decision):
    assert read(PublicSearchProgress("s1"),0,p,"entry")==decision


@pytest.mark.parametrize("patch",[
    {"status":"error"},{"status":"partial_success"},{"error":"lost"},{"harness_transport_error":"TimeoutError"},
    {"success":False},{"success":1},{"partial_success":True},{"partial_success":0},
    {"session_id":"other"},{"source_endpoint":"/private/check"},{"scope":"full_task_verdict"},
    {"progress_percentage":True},{"progress_percentage":float("nan")},{"progress_percentage":-1}])
def test_failed_or_wrong_scope_read_cannot_count_as_stagnation(patch):
    g=PublicSearchProgress("s1");read(g,0,33,"entry");read(g,10,33);before=g.snapshot()
    with pytest.raises(ValueError,match="public_progress_unavailable"):
        g.observe({**normalized_progress(33),**patch},"bad",attempts=20,trigger="periodic")
    assert g.snapshot()==before


def test_regression_and_duplicate_read_do_not_generate_fake_window():
    g=PublicSearchProgress("s1");read(g,0,33,"entry");read(g,10,33)
    with pytest.raises(ValueError,match="regressed"):read(g,20,0)
    with pytest.raises(ValueError,match="order"):read(g,10,33)
    with pytest.raises(ValueError,match="order"):
        g.observe(normalized_progress(33),"e2",attempts=20,trigger="periodic")
    assert g.stagnant_reads==1


def test_pending_frontier_read_only_and_existing_owner_geometry_guards():
    f,p,states=pure();f.observe(nearby(target()),"d1","source")
    before=f.snapshot();candidates=copy.deepcopy(p.candidates)
    assert f.pending_ids(states,p,{})==["t1"]
    assert f.snapshot()==before and p.candidates==candidates
    assert f.pending_ids(states,p,{(125.0,100.0,20.0):{}})==[]
    p.disable("d1")
    assert f.pending_ids(states,p,{})==[]


class ManyCandidates(PublicSearchPolicy):
    def __init__(self,drones,obstacles):
        super().__init__(drones,obstacles)
        self.candidates=[(float(300+i*5),100.0) for i in range(100)]
        self.candidate_cover=[frozenset({i}) for i in range(100)]
        self.covered=set()


class PlateauWorld(FlightWorld):
    def __init__(self,progress=lambda moves:33,reaches=()):
        super().__init__("hovering",20)
        for i in (3,4,5):
            self.drones[f"d{i}"]={**copy.deepcopy(self.drones["d2"]),"id":f"d{i}","name":f"Drone {i+2}"}
        self.progress,self.reaches=progress,set(reaches)
    def _call(self,name,**args):
        if name=="get_current_session":
            return self._reply(name,args,{"id":"s1","statistics":{"task_progress":{
                "task_type":"target_assignment","progress_percentage":self.progress(self.moves)}}})
        result=super()._call(name,**args)
        if name=="move_to" and self.moves in self.reaches:
            result["message"]="Drone moved to position (300,100,20) - Reached target(s): Fixed Target "+str(self.moves)
        return result


@pytest.fixture
def many(monkeypatch):
    monkeypatch.setattr(harness_skills,"PublicSearchPolicy",ManyCandidates)


@pytest.mark.parametrize("scheduler",sorted(HarnessRuntime.POLICIES))
def test_generator_handoff_inherits_budgets_and_terminal_no_replay(many,scheduler):
    r,w,_=make(PlateauWorld(),revision=V8,scheduler=scheduler)
    result,_=finish(r,"public_search")
    assert result["reason"]=="search_progress_stalled" and w.moves==result["cursor"]==20
    frame=r.frames[result["plan_id"]]
    assert (frame.api_budget,frame.repair_budget,frame.deadline)==(2000,16,500)
    assert not result["resumable"] and result["mission_completion"]=="not_evaluated"
    reject_without_effect(r,w,frame)


@pytest.mark.parametrize("revision,progress",[("observed_target_v7",33),(V8,0)])
def test_old_v7_and_zero_progress_v8_keep_full_window(many,revision,progress):
    r,w,_=make(PlateauWorld(lambda moves:progress),revision=revision,scheduler="applicability")
    result,_=finish(r,"public_search")
    assert result["reason"]=="round_limit_below_threshold" and w.moves==80
    assert len([n for n,_ in w.calls if n=="get_current_session"])==9
    if revision=="observed_target_v7":assert "search_progress" not in result["state"]


def test_new_reach_requires_public_confirmation_and_can_stop_before_next_periodic(many):
    runs=[]
    for revision in ("observed_target_v7",V8):
        r,w,_=make(PlateauWorld(lambda moves:100 if moves else 0,reaches={1}),revision=revision,scheduler="applicability")
        result,_=finish(r,"public_search");runs.append(w.moves)
        assert result["status"]=="completed" and result["state"]["progress_percent"]==100
        if revision==V8:assert result["state"]["search_progress"]["reads"][-1]["trigger"]=="new_public_reach"
    assert runs==[10,1]


def test_repeated_reach_name_does_not_add_repeated_progress_calls(many):
    class SameName(PlateauWorld):
        def _call(self,name,**args):
            result=super()._call(name,**args)
            if name=="move_to":result["message"]="Drone moved to position (300,100,20) - Reached target(s): Fixed Target 1"
            return result
    r,w,_=make(SameName(lambda moves:0),revision=V8,scheduler="applicability")
    result,_=finish(r,"public_search")
    assert result["status"]=="needs_replan" and w.moves==80
    assert len([n for n,_ in w.calls if n=="get_current_session"])==10


@pytest.mark.parametrize("mode,reason",[("unknown","unknown_action_effect_observed_do_not_replay"),
    ("partial","search_move_partial"),("wrong_actor","wrong_actor_receipt"),("effect_mismatch","search_move_effect_mismatch")])
def test_unknown_partial_effect_guards_precede_progress_policy(mode,reason):
    r,w,_=target_runtime(TargetWorld(mode=mode),revision=V8,scheduler="applicability")
    result,_=finish(r,"public_search")
    assert result["reason"]==reason and len(result["state"]["search_progress"]["reads"])==1
    reject_without_effect(r,w,r.frames[result["plan_id"]])
