"""Read-only check of connected recipes/weather/local inference. No FreshDirect calls."""
from datetime import timedelta
import json
from app.config import Settings
from app.delivery import next_preferred_delivery
from app.meals import suggest_meals

settings=Settings()
start=next_preferred_delivery(day=settings.preferred_delivery_day,hour=settings.preferred_delivery_after_hour).date()+timedelta(days=1)
context=suggest_meals(settings,start)
print(json.dumps({'start':str(start),'meals':[{'title':m['title'],'day':m['day'],'reason':m['reason'],
    'ingredients':len(m['ingredients']),'source':m['source'],'weather':m['weather']} for m in context['meals']],
    'forecast_days':len(context['forecast']),'notes':context['notes']},indent=2),flush=True)
assert len(context['meals'])==settings.meal_count, 'meal selection did not complete'
assert context['forecast'], 'live forecast not available'
