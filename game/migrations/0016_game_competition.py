# -*- coding: utf-8 -*-
# Generated by Django 1.9.4 on 2016-03-18 19:40
from __future__ import unicode_literals

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('game', '0015_auto_20160318_1955'),
    ]

    operations = [
        migrations.AddField(
            model_name='game',
            name='competition',
            field=models.ForeignKey(null=True, on_delete=django.db.models.deletion.CASCADE, to='game.Competition'),
        ),
    ]
