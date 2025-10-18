-- ------------------------------------------------------
-- DATABASE INITIALIZATION: mp3_player
-- ------------------------------------------------------

DROP DATABASE IF EXISTS `mp3_player`;

CREATE DATABASE `mp3_player`
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE `mp3_player`;

SET @OLD_FOREIGN_KEY_CHECKS=@@FOREIGN_KEY_CHECKS;
SET FOREIGN_KEY_CHECKS=0;

-- ------------------------------------------------------
-- USERS
-- ------------------------------------------------------
CREATE TABLE `users` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(255) COLLATE utf8mb4_unicode_ci UNIQUE,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- TRACKS (pure metadata table)
-- ------------------------------------------------------
CREATE TABLE `tracks` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `spotify_id` VARCHAR(255) COLLATE utf8mb4_unicode_ci UNIQUE,
  `name` VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `artist` VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `album` VARCHAR(255) COLLATE utf8mb4_unicode_ci,
  `year` VARCHAR(10) COLLATE utf8mb4_unicode_ci,
  `duration_ms` INT,
  `checksum` VARCHAR(255) COLLATE utf8mb4_unicode_ci,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- ARTISTS
-- ------------------------------------------------------
CREATE TABLE IF NOT EXISTS youtube_channels (
    id INT AUTO_INCREMENT PRIMARY KEY,
    artist_name VARCHAR(255) NOT NULL UNIQUE,
    channel_url VARCHAR(512),
    last_checked DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- ------------------------------------------------------
-- DOWNLOADS
-- ------------------------------------------------------
CREATE TABLE `downloads` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT,
  `track_id` INT,
  `youtube_id` VARCHAR(255) COLLATE utf8mb4_unicode_ci,
  `filepath` TEXT COLLATE utf8mb4_unicode_ci,
  `bitrate` INT,
  `filesize_mb` DECIMAL(10,2),
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  KEY `track_id` (`track_id`),
  CONSTRAINT `downloads_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `downloads_ibfk_2` FOREIGN KEY (`track_id`) REFERENCES `tracks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- BAD_VIDEOS (normalized)
-- ------------------------------------------------------
CREATE TABLE `bad_videos` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `track_id` INT NOT NULL,
  `youtube_id` VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `youtube_title` VARCHAR(512) COLLATE utf8mb4_unicode_ci,
  `reason` TEXT COLLATE utf8mb4_unicode_ci,
  `flagged_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `unique_bad_video` (`user_id`,`track_id`,`youtube_id`),
  CONSTRAINT `bad_videos_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `bad_videos_ibfk_2` FOREIGN KEY (`track_id`) REFERENCES `tracks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- PLAYLISTS + RELATIONS
-- ------------------------------------------------------
CREATE TABLE `playlists` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT,
  `name` VARCHAR(255) COLLATE utf8mb4_unicode_ci NOT NULL,
  `spotify_id` VARCHAR(255) COLLATE utf8mb4_unicode_ci UNIQUE,
  `created_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  KEY `user_id` (`user_id`),
  CONSTRAINT `playlists_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE `playlist_tracks` (
  `playlist_id` INT NOT NULL,
  `track_id` INT NOT NULL,
  `track_number` INT,
  PRIMARY KEY (`playlist_id`,`track_id`),
  CONSTRAINT `playlist_tracks_ibfk_1` FOREIGN KEY (`playlist_id`) REFERENCES `playlists` (`id`) ON DELETE CASCADE,
  CONSTRAINT `playlist_tracks_ibfk_2` FOREIGN KEY (`track_id`) REFERENCES `tracks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- FAVORITES
-- ------------------------------------------------------
CREATE TABLE `favorites` (
  `user_id` INT NOT NULL,
  `track_id` INT NOT NULL,
  `added_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`,`track_id`),
  CONSTRAINT `favorites_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `favorites_ibfk_2` FOREIGN KEY (`track_id`) REFERENCES `tracks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- USER HISTORY
-- ------------------------------------------------------
CREATE TABLE `user_history` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `user_id` INT NOT NULL,
  `track_id` INT NOT NULL,
  `action` ENUM('played','downloaded','deleted','skipped') COLLATE utf8mb4_unicode_ci,
  `timestamp` TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  CONSTRAINT `user_history_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE,
  CONSTRAINT `user_history_ibfk_2` FOREIGN KEY (`track_id`) REFERENCES `tracks` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- USER SETTINGS
-- ------------------------------------------------------
CREATE TABLE `user_settings` (
  `user_id` INT NOT NULL,
  `theme` VARCHAR(20) COLLATE utf8mb4_unicode_ci DEFAULT 'light',
  `language` VARCHAR(20) COLLATE utf8mb4_unicode_ci DEFAULT 'en',
  `default_download_path` TEXT COLLATE utf8mb4_unicode_ci,
  `audio_quality` ENUM('low','medium','high','max') COLLATE utf8mb4_unicode_ci DEFAULT 'high',
  `trim_silence` TINYINT(1) DEFAULT 1,
  `auto_tag` TINYINT(1) DEFAULT 1,
  PRIMARY KEY (`user_id`),
  CONSTRAINT `user_settings_ibfk_1` FOREIGN KEY (`user_id`) REFERENCES `users` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- ------------------------------------------------------
-- RESET FOREIGN KEY CHECKS
-- ------------------------------------------------------
SET FOREIGN_KEY_CHECKS=@OLD_FOREIGN_KEY_CHECKS;
