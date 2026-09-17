-- Tower Static: a radio station on channel 501 with four tracks.
-- The two radio tables have no primary key, so every row is only added when
-- it is not there yet - saving again never adds a second copy.

INSERT INTO master_radio_music (music_id, title, artist) SELECT 'SN_BGM_Radio_Music_0901', 'Tower of Barbs - One More Floor', 'Tower Static' WHERE NOT EXISTS (SELECT 1 FROM master_radio_music WHERE music_id = 'SN_BGM_Radio_Music_0901');
INSERT INTO master_radio_music (music_id, title, artist) SELECT 'SN_BGM_Radio_Music_0902', 'Mushroom Club - Premium Survival', 'Tower Static' WHERE NOT EXISTS (SELECT 1 FROM master_radio_music WHERE music_id = 'SN_BGM_Radio_Music_0902');
INSERT INTO master_radio_music (music_id, title, artist) SELECT 'SN_BGM_Radio_Music_0903', 'Waiting Room - Death Drive', 'Tower Static' WHERE NOT EXISTS (SELECT 1 FROM master_radio_music WHERE music_id = 'SN_BGM_Radio_Music_0903');
INSERT INTO master_radio_music (music_id, title, artist) SELECT 'SN_BGM_Radio_Music_0904', 'Momoko - Stir the Odds', 'Tower Static' WHERE NOT EXISTS (SELECT 1 FROM master_radio_music WHERE music_id = 'SN_BGM_Radio_Music_0904');
INSERT INTO master_radio_channel (channel, music_id1, music_id2, music_id3, music_id4, music_id5, channel_name, rand_grp, platform) SELECT '501', 'SN_BGM_Radio_Music_0901', 'SN_BGM_Radio_Music_0902', 'SN_BGM_Radio_Music_0903', 'SN_BGM_Radio_Music_0904', '', 'Tower Static', 0, 0 WHERE NOT EXISTS (SELECT 1 FROM master_radio_channel WHERE channel = '501');
