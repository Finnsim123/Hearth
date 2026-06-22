from __future__ import annotations
import pandas as pd
from hearth.domain.inference.predictor import predict_person, FACT_VERSION, RULES_VERSION
from hearth.domain.schemas import Activity, Binding, Role

def _feats(home_last_values):
    idx = pd.date_range("2026-06-01 12:00", periods=len(home_last_values), freq="30min", tz="UTC")
    return pd.DataFrame({"alice_loc_home_last": home_last_values}, index=idx)

class FakeTSDB:
    def __init__(self, feats): self.feats=feats; self.written=[]
    def read_features(self,*a,**k): return self.feats
    def read_predictions(self,*a,**k): return []
    def read_labels(self,*a,**k): return []
    def write_prediction(self,pred): self.written.append(pred)
    def write_label(self,*a,**k): pass
    def write_heartbeat(self,*a,**k): pass

class FakeStore:
    def __init__(self): self.loads=0
    def load(self,rec): self.loads+=1; raise AssertionError("model must be skipped when away")

class FakeRepo:
    def __init__(self,bindings): self._b=bindings
    def bindings(self): return self._b
    def models(self,p=None): return []
    def activities(self): return [Activity(slug="home",name="Home"), Activity(slug="away",name="Away")]
    def rules(self): return []
    def get_setting(self,k,d=None): return d

BIND=[Binding(entity_id="person.alice",role=Role.PERSON,name="alice_loc",person_id="alice")]

def test_away_is_fact_and_skips_model():
    ts=FakeTSDB(_feats([0.0,0.0])); store=FakeStore()
    preds=predict_person("alice",ts,FakeRepo(BIND),store)
    assert len(preds)==2
    assert all(p.model_version==FACT_VERSION and p.predicted=="away" and p.confidence==1.0 for p in preds)
    assert store.loads==0   # model never loaded for away windows

def test_home_runs_model_path_and_never_says_away():
    ts=FakeTSDB(_feats([1.0,1.0]))
    preds=predict_person("alice",ts,FakeRepo(BIND),FakeStore())
    assert len(preds)==2
    assert all(p.model_version==RULES_VERSION for p in preds)   # no promoted model → rules
    assert all(p.predicted!="away" for p in preds)              # home-gate zeroed away

def test_mixed_window_by_window():
    ts=FakeTSDB(_feats([0.0,1.0]))
    preds=predict_person("alice",ts,FakeRepo(BIND),FakeStore())
    assert preds[0].predicted=="away" and preds[0].model_version==FACT_VERSION
    assert preds[1].predicted!="away" and preds[1].model_version==RULES_VERSION


class RepoWithFacts(FakeRepo):
    def __init__(self, bindings, settings):
        super().__init__(bindings); self._st = settings
    def get_setting(self, k, d=None): return self._st.get(k, d)

def test_earned_sleep_fact_bypasses_model_when_home():
    idx = pd.date_range("2026-06-02 02:00", periods=2, freq="30min", tz="UTC")
    feats = pd.DataFrame({"alice_loc_home_last": [1.0, 1.0],   # home (not away)
                          "bed_occupied": [1.0, 1.0]}, index=idx)  # in bed
    from hearth.domain.schemas import Binding, Role
    binds = [Binding(entity_id="person.alice", role=Role.PERSON, name="alice_loc", person_id="alice"),
             Binding(entity_id="sensor.bed", role=Role.BED, name="bed", person_id="alice")]
    settings = {"foundational.facts": [{"id": "alice:asleep", "gate": "asleep",
                "binding_name": "bed", "role": "bed", "person_id": "alice", "enabled": True}],
                "foundational.verdicts": {"alice:asleep": {"role_decision": "fact"}}}
    ts = FakeTSDB(feats)
    preds = predict_person("alice", ts, RepoWithFacts(binds, settings), FakeStore())
    assert all(p.predicted == "asleep" and p.model_version == FACT_VERSION for p in preds)
