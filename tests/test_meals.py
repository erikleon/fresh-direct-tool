from datetime import date,datetime,timedelta,timezone
import io,json
from types import SimpleNamespace
import pytest
from app import meals,ai
from app.config import Settings
from app.budget import DraftPlan
from app.plans import save_draft,get_meal_context,discard

RECIPE='# Potato soup\n\nServes 4. Takes 30 minutes.\n\n## Ingredients\n- 2 lb potatoes\n- 1 onion\n- 4 cups broth\n\n## Steps\n1. Simmer.\n'


def settings(tmp_path,content=RECIPE,**kwargs):
    path=tmp_path/'recipes.json';path.write_text(json.dumps({'exported_at':datetime.now(timezone.utc).isoformat(),
        'recipes':[{'id':'soup','name':'soup.md','content':content}]}))
    return Settings(data_dir=tmp_path,recipes_file=path,**kwargs)


def test_recipe_quantities_are_exact_and_stale_export_is_rejected(tmp_path):
    config=settings(tmp_path);rows,note=meals.load_recipes(config)
    assert note is None and rows[0]['ingredients']==['2 lb potatoes','1 onion','4 cups broth']
    data=json.loads(config.recipes_file.read_text());data['exported_at']=(datetime.now(timezone.utc)-timedelta(days=3)).isoformat()
    config.recipes_file.write_text(json.dumps(data))
    assert meals.load_recipes(config)[0]==[]


def test_grill_and_soup_cues_use_temperature_rain_and_wind(monkeypatch,tmp_path):
    config=settings(tmp_path,weather_latitude=1,weather_longitude=1)
    payload={'daily':{'time':['2026-10-21','2026-10-22','2026-10-23','2026-10-24'],
        'temperature_2m_max':[75,48,75,75],'temperature_2m_min':[60,35,60,60],
        'precipitation_probability_max':[5,75,5,5],'wind_gusts_10m_max':[10,20,35,10],
        'weather_code':[1,61,1,95]}}
    monkeypatch.setattr(meals.urllib.request,'urlopen',lambda *a,**k:io.BytesIO(json.dumps(payload).encode()))
    forecast,note=meals.weather(config,date(2026,10,21),4)
    assert [d['cooking_hint'] for d in forecast]==['grill','soup','indoor','indoor']
    assert note is None


def test_unknown_recipe_date_and_duplicates_are_rejected(tmp_path,monkeypatch):
    config=settings(tmp_path)
    monkeypatch.setattr(meals,'weather',lambda *a:([], 'Weather unavailable'))
    choice=meals.MealChoices(meals=[meals.MealChoice(recipe_id='invented',day='2026-10-21',reason='made up'),
        meals.MealChoice(recipe_id='soup',day='2026-10-21',reason='House soup'),
        meals.MealChoice(recipe_id='soup',day='2026-10-22',reason='duplicate')])
    monkeypatch.setattr(ai,'_ask_json',lambda *a:choice)
    context=meals.suggest_meals(config,date(2026,10,21))
    assert len(context['meals'])==1 and context['meals'][0]['ingredients'][0]=='2 lb potatoes'
    assert context['meals'][0]['weather'] is None
    assert 'Weather unavailable' in context['notes']


def test_grill_recipe_requires_suitable_forecast(tmp_path,monkeypatch):
    config=settings(tmp_path,content=RECIPE.replace('Potato soup','Grilled potatoes'))
    monkeypatch.setattr(meals,'weather',lambda *a:([], 'Weather unavailable'))
    monkeypatch.setattr(ai,'_ask_json',lambda *a:meals.MealChoices(meals=[meals.MealChoice(recipe_id='soup',day='2026-10-21',reason='grill')]))
    assert meals.suggest_meals(config,date(2026,10,21))['meals']==[]


def test_ai_failure_is_visible_without_losing_staple_plan(tmp_path,monkeypatch):
    config=settings(tmp_path)
    monkeypatch.setattr(meals,'weather',lambda *a:([],None))
    monkeypatch.setattr(ai,'_ask_json',lambda *a:None)
    context=meals.suggest_meals(config,date(2026,10,21))
    assert context['meals']==[] and any('Local AI unavailable' in n for n in context['notes'])
    plan_id=save_draft(DraftPlan(week_of=date.today(),meal_context=context),config)
    assert get_meal_context(plan_id,config)==context
    assert discard(plan_id,config)
    assert get_meal_context(plan_id,config)=={}


def test_disabled_meals_make_no_context_or_model_calls(tmp_path,monkeypatch):
    config=settings(tmp_path,meal_count=0)
    monkeypatch.setattr(meals,'load_recipes',lambda *a:pytest.fail('recipes read'))
    assert meals.suggest_meals(config,date.today())['meals']==[]


@pytest.mark.parametrize('line,query',[('2 tablespoons olive oil, divided','olive oil'),('1/2 cup milk','milk'),('4 cups broth','broth'),('1 onion','onion')])
def test_search_query_keeps_ingredient(line,query):
    assert meals.ingredient_query(line)==query


def test_malformed_recipe_does_not_hide_valid_nested_recipe(tmp_path):
    config=settings(tmp_path,content='# Chicken\n## Ingredients\n### Sauce\n- 1 (14 oz) can tomatoes\n### Chicken\n- 2 lb chicken\n## Steps\n- Use a grill pan.\n')
    data=json.loads(config.recipes_file.read_text())
    data['recipes'].insert(0,{'id':'bad'})
    config.recipes_file.write_text(json.dumps(data))
    recipes,note=meals.load_recipes(config)
    assert len(recipes)==1 and '1 unreadable' in note
    assert recipes[0]['summary']=='' and recipes[0]['style']=='indoor'
    assert recipes[0]['ingredients']==['1 (14 oz) can tomatoes','2 lb chicken']
    assert meals.ingredient_query(recipes[0]['ingredients'][0])=='tomatoes'


def test_large_collection_is_bounded_before_model_request(tmp_path,monkeypatch):
    config=settings(tmp_path)
    data=json.loads(config.recipes_file.read_text())
    data['recipes']=[{'id':str(i),'name':f'{i}.md','content':RECIPE.replace('Serves 4.', 'Long intro '*100)} for i in range(100)]
    config.recipes_file.write_text(json.dumps(data))
    monkeypatch.setattr(meals,'weather',lambda *a:([],None))
    def ask(system,payload,schema,settings):
        assert 0<len(payload['recipes'])<100
        assert ai.input_size(system,payload)<=settings.ai_context_tokens-4096
        return meals.MealChoices(meals=[])
    monkeypatch.setattr(ai,'_ask_json',ask)
    context=meals.suggest_meals(config,date.today())
    assert any('fit the local model context' in n for n in context['notes'])


def test_weather_sends_only_approximate_coordinates(tmp_path,monkeypatch):
    config=settings(tmp_path,weather_latitude=40.123456,weather_longitude=-73.987654)
    seen=[]
    def fetch(url,**kwargs):
        seen.append(url)
        return io.BytesIO(b'{"daily":{"time":[]}}')
    monkeypatch.setattr(meals.urllib.request,'urlopen',fetch)
    meals.weather(config,date.today(),1)
    assert 'latitude=40.12' in seen[0] and 'longitude=-73.99' in seen[0]
    assert '123456' not in seen[0]


def test_cut_weather_response_does_not_stop_recipe_planning(tmp_path, monkeypatch):
    from http.client import IncompleteRead
    config = settings(tmp_path, weather_latitude=1, weather_longitude=1)
    class Broken(io.BytesIO):
        def read(self, *args):
            raise IncompleteRead(b'partial')
    monkeypatch.setattr(meals.urllib.request, 'urlopen', lambda *a, **k: Broken())
    forecast, note = meals.weather(config, date.today(), 7)
    assert forecast == [] and 'unavailable' in note
