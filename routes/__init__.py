"""
Tüm Blueprint'leri tek fonksiyondan kayıt eder.
app.py sadece register_blueprints(app) çağırır.
"""

from routes.auth import bp as auth_bp
from routes.dashboard import bp as dashboard_bp
from routes.stats import bp as stats_bp
from routes.songs import bp as songs_bp
from routes.playlists import bp as playlists_bp
from routes.ai import bp as ai_bp
from routes.system import bp as system_bp
from routes.topluluk import bp as topluluk_bp
from routes.pets import bp as pets_bp


def register_blueprints(app):
    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(stats_bp)
    app.register_blueprint(songs_bp)
    app.register_blueprint(playlists_bp)
    app.register_blueprint(ai_bp)
    app.register_blueprint(system_bp)
    app.register_blueprint(topluluk_bp)
    app.register_blueprint(pets_bp)
