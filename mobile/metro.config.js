// Default Expo Metro config. Kept explicit so future customization (svg transformer, monorepo
// resolution against the sibling frontend/ package) has a home.
const { getDefaultConfig } = require('expo/metro-config')

const config = getDefaultConfig(__dirname)

module.exports = config
